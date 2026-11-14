"""Detector selection and extension contract; no audio engine or hardware needed."""

from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from nfs.capture_detection import CaptureContext, CaptureIssue, DEFAULT_DETECTORS, find_issue


@pytest.mark.parametrize('detector', DEFAULT_DETECTORS, ids=lambda d: d.name)
def test_each_detector_can_be_disabled_without_running_it(detector):
    issue = CaptureIssue(detector.name, 'test rejection')
    check = Mock(return_value=issue)
    detectors = (replace(detector, check=check),)
    context = CaptureContext({}, 48000, {}, metrics={})
    assert find_issue(detectors, detector.stage, context) is issue
    check.assert_called_once_with(context)
    check.reset_mock()
    disabled = replace(context, settings={detector.enabled_key: False})
    assert find_issue(detectors, detector.stage, disabled) is None
    check.assert_not_called()


def test_stage_selection_and_first_rejection_short_circuit():
    checks = [Mock(return_value=None), Mock(return_value=CaptureIssue('second', 'bad')),
              Mock(side_effect=AssertionError('must not run'))]
    detectors = tuple(replace(DEFAULT_DETECTORS[0], check=check) for check in checks)
    context = CaptureContext({}, 48000, {}, metrics={})
    assert find_issue(detectors, 'metrics', context) is None
    for check in checks:
        check.assert_not_called()
    assert find_issue(detectors, 'raw', context).reason == 'bad'
    checks[0].assert_called_once_with(context)
    checks[1].assert_called_once_with(context)
    checks[2].assert_not_called()


def test_detectors_do_not_modify_recording_arrays():
    signal = np.ones(48000, dtype=np.float32)
    signal[12000:14000] = 0
    signal.flags.writeable = False
    capture = {'raw_recordings': (signal, signal), 'capture_metadata': {
        'aligned_repeats': [{'sweep_start_sample': 0}],
        'sweep_samples': len(signal), 'guard_samples': 480,
    }}
    context = CaptureContext(capture, 48000, {}, {'psr': 5, 'thd_pct': 0})
    for detector in DEFAULT_DETECTORS:
        detector.check(context)


def test_detector_errors_propagate_instead_of_accepting_capture():
    check = Mock(side_effect=RuntimeError('broken detector'))
    with pytest.raises(RuntimeError, match='broken detector'):
        find_issue((replace(DEFAULT_DETECTORS[0], check=check),),
                   'raw', CaptureContext({}, 48000, {}))


def test_metrics_stage_requires_metrics():
    with pytest.raises(ValueError, match='require calculated metrics'):
        find_issue(DEFAULT_DETECTORS, 'metrics', CaptureContext({}, 48000, {}))
