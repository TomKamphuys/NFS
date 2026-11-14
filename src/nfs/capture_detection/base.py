"""In-memory contract shared by capture detectors and their caller.

Detectors must treat the context (including NumPy arrays) as read-only. They
report a rejection or return None; the caller owns retries and artifact saving.
Exceptions propagate: a detector programming error must not silently accept data.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

Stage = Literal["raw", "metrics"]


@dataclass(frozen=True)
class CaptureContext:
    capture: Mapping[str, Any]
    fs: int
    settings: Mapping[str, Any]
    metrics: Mapping[str, float] | None = None
    ir_full: np.ndarray | None = None
    ir_linear: np.ndarray | None = None


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


def find_issue(detectors: Sequence[CaptureDetector], stage: Stage,
               context: CaptureContext) -> CaptureIssue | None:
    """Return the first rejection from enabled detectors at this stage."""
    if stage == "metrics" and context.metrics is None:
        raise ValueError("Metric detectors require calculated metrics")
    for detector in detectors:
        if detector.stage == stage and context.settings.get(detector.enabled_key, True):
            issue = detector.check(context)
            if issue is not None:
                return issue
    return None
