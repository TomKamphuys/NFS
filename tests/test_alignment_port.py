"""Marker selection must prefer waveform match over signal level."""
import numpy as np

from nfs.audio import AlignmentEngine, MarkerGenerator


def test_normalized_detection_rejects_louder_imperfect_match():
    fs = 48000
    marker = MarkerGenerator(fs, 100, (500, 5000), -6).generate()
    noise = np.random.default_rng(71).normal(size=len(marker))
    noise -= marker * (np.dot(noise, marker) / np.dot(marker, marker))
    noise *= np.linalg.norm(marker) / np.linalg.norm(noise)
    recording = np.zeros(fs)
    recording[6000:6000 + len(marker)] = marker
    recording[18000:18000 + len(marker)] = 10 * (.8 * marker + .6 * noise)
    engine = AlignmentEngine(fs, 1, False, 20, 100)

    raw = engine._matched_filter_detect(recording, marker)
    normalized = engine._matched_filter_detect(recording, marker, normalize=True)

    assert raw[0] != 6000
    assert normalized[0] == 6000


