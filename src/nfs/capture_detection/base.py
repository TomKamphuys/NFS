"""In-memory contract shared by capture detectors and their caller.

Detectors must treat the context (including NumPy arrays) as read-only. They
report a rejection or return None; the caller owns retries and artifact saving.
Exceptions propagate: a detector programming error must not silently accept data.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from loguru import logger
from .stream_health import stream_fault
from .events import DetectorDecision, DetectorEvent

Stage = Literal["raw", "metrics"]


@dataclass(frozen=True)
class CaptureContext:
    capture: Mapping[str, Any]
    fs: int
    settings: Mapping[str, Any]
    metrics: Mapping[str, float] | None = None
    ir_full: np.ndarray | None = None
    ir_linear: np.ndarray | None = None
    detector_state: Mapping[str, Any] = field(default_factory=dict)
    measurement_set: bool = False
    analysis_cache: dict[str, Any] = field(default_factory=dict, compare=False)
    disabled_detectors: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CaptureIssue:
    detector: str
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureDetector:
    name: str
    stage: Stage
    check: Callable[[CaptureContext], CaptureIssue | None]
    enabled_key: str
    label: str
    description: str
    default_enabled: bool = True
    group: str = "Tom's detectors"
    prepare: Callable[["DetectorSession"], Any] | None = None
    archive: Callable[[Any], Mapping[str, np.ndarray]] | None = None
    preparation_failure_message: str = 'Detector preparation failed.'
    setup_hint: str = 'Review the detector settings.'


class DetectorPreparationCancelled(Exception):
    """The user stopped a set while detector reference captures were running."""


@dataclass(frozen=True)
class DetectorSession:
    fs: int
    settings: Mapping[str, Any]
    acquire: Callable[..., Mapping[str, Any]]
    report: Callable[[DetectorEvent], None] = lambda event: None
    interactive: bool = False
    capture_duration_s: float = 0.0
    disabled_detectors: set[str] = field(default_factory=set, compare=False)
    single_capture_duration_s: float = 0.0


def prepare_detectors(detectors: Sequence[CaptureDetector], session: DetectorSession) -> dict[str, Any]:
    """Prepare enabled stateful detectors once, before measuring any points."""
    state = {}
    for detector in detectors:
        if detector.prepare is None or not session.settings.get(detector.enabled_key, detector.default_enabled):
            continue
        try:
            state[detector.name] = detector.prepare(session)
        except ValueError as error:
            if not session.interactive:
                raise
            decision = DetectorDecision()
            session.report(DetectorEvent(
                'preparation_failed', detector.label, detector.preparation_failure_message,
                detector=detector.name,
                details=f'{error}\n\n{detector.setup_hint} Continuing disables this detector for this scan only.',
                actions=(('disable_for_scan', 'Continue without this detector'), ('cancel', 'Cancel scan')),
                decision=decision))
            if decision.choice != 'disable_for_scan':
                raise DetectorPreparationCancelled() from error
            session.disabled_detectors.add(detector.name)
    return state


def archive_detector_state(detectors: Sequence[CaptureDetector],
                           state: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """Collect optional plugin arrays for the caller's existing NPZ archive."""
    arrays = {}
    for detector in detectors:
        if detector.archive is not None and detector.name in state:
            fields = detector.archive(state[detector.name])
            if arrays.keys() & fields.keys():
                raise ValueError('Detector archive field names must be unique')
            arrays.update(fields)
    return arrays


def stream_health(context: CaptureContext) -> CaptureIssue | None:
    fault = stream_fault(context.capture.get('capture_metadata', {}).get('stream_status', ()))
    if fault:
        logger.warning(fault)
        return CaptureIssue('stream_health', fault)
    return None


def find_issue(detectors: Sequence[CaptureDetector], stage: Stage,
               context: CaptureContext) -> CaptureIssue | None:
    """Return the first rejection from enabled detectors at this stage."""
    if stage == "raw":
        issue = stream_health(context)
        if issue:
            return issue
    if stage == "metrics" and context.metrics is None:
        raise ValueError("Metric detectors require calculated metrics")
    for detector in detectors:
        if (detector.stage == stage
                and detector.name not in context.disabled_detectors
                and context.settings.get(detector.enabled_key, detector.default_enabled)):
            issue = detector.check(context)
            if issue is not None:
                return issue
    return None
