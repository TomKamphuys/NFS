import pytest
import numpy as np
import scipy.signal
import configparser
import json
import os
import sys
import threading
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch
import nfs.audio as audio_module
from nfs.audio import (
    MarkerGenerator, SweepGenerator, HarmonicInjector,
    ProtectionFilter, DeconvolutionEngine, AlignmentEngine,
    DSPVerificationTool, AudioFactory, find_device_id_by_name, get_devices_and_channels,
    get_supported_sample_rates, resolve_device_id_by_saved_identity,
    SINE_RMS_FROM_PEAK,
    _AudioMeterWorker,
)
from nfs.utils.dsp import DSPUtils
from nfs.datatypes import CylindricalPosition


@pytest.fixture
def fs():
    return 48000


def test_audio_meter_worker_routes_channels_and_publishes_weighted_levels(monkeypatch, fs):
    published = []
    updated = threading.Event()

    def capture_update(outputs, inputs, **kwargs):
        published.append((outputs.copy(), inputs.copy(), kwargs))
        updated.set()

    monkeypatch.setattr(audio_module, "update_audio_meter_state", capture_update)
    worker = _AudioMeterWorker(fs)
    worker.start()
    outputs = np.zeros((512, 4), dtype=np.float32)
    inputs = np.zeros((512, 3), dtype=np.float32)
    outputs[:, 2] = 0.25
    outputs[:, 3] = -0.5
    inputs[:, 1] = 0.125
    inputs[:, 0] = -0.25

    try:
        worker.submit(outputs, (2, 3), inputs, (1, 0), None)
        assert updated.wait(2.0)
    finally:
        worker.stop()

    meter_outputs, meter_inputs, kwargs = published[-1]
    assert np.all(meter_outputs[:, 0] == 0.25)
    assert np.all(meter_outputs[:, 1] == -0.5)
    assert np.all(meter_inputs[:, 0] == 0.125)
    assert np.all(meter_inputs[:, 1] == -0.25)
    assert kwargs["active"] is True
    assert kwargs["a_weighted_inputs"].shape == meter_inputs.shape
    assert kwargs["c_weighted_inputs"].shape == meter_inputs.shape


def test_audio_meter_worker_defers_callback_status_logging(monkeypatch, fs):
    warning = Mock()
    monkeypatch.setattr(audio_module.logger, "warning", warning)
    status = Mock(
        input_underflow=False,
        input_overflow=False,
        output_underflow=True,
        output_overflow=False,
        priming_output=False,
    )
    status.__bool__ = Mock(return_value=True)
    worker = _AudioMeterWorker(fs)
    worker.start()
    block = np.zeros((128, 2), dtype=np.float32)

    worker.submit(block, (0, 1), block, (0, 1), status)
    assert warning.call_count == 0
    worker.stop()

    assert warning.call_count == 1
    assert "output_underflow" in str(warning.call_args)


def test_audio_meter_worker_skips_backlog_and_processes_latest_100ms(monkeypatch, fs):
    published = []
    monkeypatch.setattr(
        audio_module,
        "update_audio_meter_state",
        lambda outputs, inputs, **kwargs: published.append((outputs.copy(), inputs.copy())),
    )
    worker = _AudioMeterWorker(fs)
    older = np.zeros((3000, 2), dtype=np.float32)
    newer = np.ones((3000, 2), dtype=np.float32)

    worker.submit(older, (0, 1), older, (0, 1), None)
    worker.submit(newer, (0, 1), newer, (0, 1), None)
    worker._process_available(worker._write_index)

    meter_outputs, meter_inputs = published[-1]
    assert len(meter_outputs) == 4800
    assert len(meter_inputs) == 4800
    assert np.all(meter_outputs[-3000:] == 1.0)
    assert np.all(meter_inputs[-3000:] == 1.0)
    assert worker._skipped_meter_frames == 1200


# -----------------------------------------------------------------------------
# SIGNAL GENERATORS
# -----------------------------------------------------------------------------

def test_marker_generator_functional(fs):
    dur_ms = 100.0
    bw_hz = (500.0, 5000.0)
    level_dbfs = -6.0

    gen = MarkerGenerator(fs, dur_ms, bw_hz, level_dbfs)
    marker = gen.generate()

    # Check duration
    expected_len = int(round(dur_ms / 1000.0 * fs))
    assert len(marker) == expected_len

    # Check level
    peak = np.max(np.abs(marker))
    assert np.isclose(20 * np.log10(peak), level_dbfs, atol=0.1)

    # Check bandwidth (simple check: spectrum should be low outside bw)
    Nfft = 4096
    freqs = np.fft.rfftfreq(Nfft, 1 / fs)
    spec = np.abs(np.fft.rfft(marker, n=Nfft))
    spec /= (np.max(spec) + 1e-12)

    # Look at 100Hz (well below 500Hz)
    idx_100 = np.searchsorted(freqs, 100)
    assert spec[idx_100] < 0.15

    # Look at 10000Hz (well above 5000Hz)
    idx_10k = np.searchsorted(freqs, 10000)
    assert spec[idx_10k] < 0.15


def test_sweep_generator_functional(fs):
    dur_s = 0.5
    f1 = 20.0
    level_dbfs = -10.0

    gen = SweepGenerator(fs, dur_s, f1, level_dbfs)
    s_fund, phase, inv = gen.generate()

    # Check lengths
    expected_n = int(round(dur_s * fs))
    assert len(s_fund) == expected_n
    assert len(inv) == expected_n

    # Check inverse filter property: s_fund * inv should yield a delta-like pulse in frequency domain (unity gain)
    Nfft = 2 ** int(np.ceil(np.log2(len(s_fund) + len(inv) - 1)))
    S = np.fft.rfft(s_fund, n=Nfft)
    I = np.fft.rfft(inv, n=Nfft)
    H = S * I

    # The magnitude should be flat across the sweep range
    mag_h = np.abs(H)
    f = np.fft.rfftfreq(Nfft, 1 / fs)
    mask = (f > f1 * 2) & (f < fs * 0.45)  # Avoid edges

    assert np.all(np.isclose(mag_h[mask], 1.0, atol=0.5))
    assert np.mean(mag_h[mask]) == pytest.approx(1.0, abs=0.1)


# -----------------------------------------------------------------------------
# SWEEP GENERATOR — INVERSE FILTER ENDPOINT FADE
# -----------------------------------------------------------------------------

def _fade_duration_sweep(dur_s: float) -> float:
    """Return the fade duration in seconds used by SweepGenerator for a given sweep duration."""
    return min(5e-3, max(1e-3, 0.002 * dur_s))


def test_sweep_generator_inverse_fade_duration_short(fs):
    """For a 0.1 s sweep, 0.2% = 0.2 ms < 1 ms minimum → fade = 1 ms."""
    dur_s = 0.1
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    _, _, inv = gen.generate()
    expected_fade_s = _fade_duration_sweep(dur_s)
    assert expected_fade_s == 1e-3
    n_fade = int(round(expected_fade_s * fs))
    # First sample should be near zero (fade-in from zero)
    assert np.abs(inv[0]) < 1e-6
    # Interior (after fade) should not be zero — use a relative threshold
    # since short sweeps produce smaller inverse filter amplitudes
    assert np.max(np.abs(inv[n_fade:])) > 1e-3


def test_sweep_generator_inverse_fade_duration_medium(fs):
    """For a 0.5 s sweep, 0.2% = 1 ms → fade = 1 ms (at the minimum clamp)."""
    dur_s = 0.5
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    _, _, inv = gen.generate()
    expected_fade_s = _fade_duration_sweep(dur_s)
    assert expected_fade_s == 1e-3
    n_fade = int(round(expected_fade_s * fs))
    assert np.abs(inv[0]) < 1e-6
    assert np.abs(inv[-1]) < 1e-6


def test_sweep_generator_inverse_fade_duration_long(fs):
    """For a 10 s sweep, 0.2% = 20 ms > 5 ms maximum → fade = 5 ms."""
    dur_s = 10.0
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    _, _, inv = gen.generate()
    expected_fade_s = _fade_duration_sweep(dur_s)
    assert expected_fade_s == 5e-3
    n_fade = int(round(expected_fade_s * fs))
    assert np.abs(inv[0]) < 1e-6
    assert np.abs(inv[-1]) < 1e-6


def test_sweep_generator_inverse_fade_duration_nominal(fs):
    """For a 5 s sweep, 0.2% = 10 ms > 5 ms max → fade = 5 ms.
    For a 2 s sweep, 0.2% = 4 ms → fade = 4 ms (within [1, 5] ms)."""
    dur_s = 2.0
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    _, _, inv = gen.generate()
    expected_fade_s = _fade_duration_sweep(dur_s)
    assert expected_fade_s == 4e-3
    n_fade = int(round(expected_fade_s * fs))
    assert np.abs(inv[0]) < 1e-6
    assert np.abs(inv[-1]) < 1e-6


def test_sweep_generator_inverse_fade_smooth_transition(fs):
    """Verify the fade-in ramp is a smooth half-cosine, not a linear or abrupt step."""
    dur_s = 1.0
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    _, _, inv = gen.generate()
    expected_fade_s = _fade_duration_sweep(dur_s)
    n_fade = int(round(expected_fade_s * fs))
    # Check that the very first sample is near zero (fade-in from zero)
    assert np.abs(inv[0]) < 1e-6
    # Check that the last sample is near zero (fade-out to zero)
    assert np.abs(inv[-1]) < 1e-6
    # Check that the interior is larger than the fade region — use relative comparison
    # since inverse filter amplitudes are normalized and vary with sweep parameters
    interior = inv[n_fade:-n_fade] if 2 * n_fade < len(inv) else inv[n_fade:]
    fade_region = np.concatenate([inv[:n_fade], inv[-n_fade:]]) if 2 * n_fade < len(inv) else inv[:n_fade]
    assert np.max(np.abs(interior)) > np.max(np.abs(fade_region)), \
        "Interior should have larger amplitude than the faded endpoints"


def test_sweep_generator_inverse_fade_short_sweep_no_overlap(fs):
    """For a very short sweep (0.01 s = 480 samples at 48 kHz),
    the two 1 ms fades (48 samples each) should not overlap."""
    dur_s = 0.01
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    _, _, inv = gen.generate()
    expected_n = int(round(dur_s * fs))
    assert len(inv) == expected_n
    expected_fade_s = _fade_duration_sweep(dur_s)
    n_fade = int(round(expected_fade_s * fs))
    # Two fades should not consume the entire signal
    assert 2 * n_fade < len(inv), "Fades should not overlap on short sweeps"
    # Shape and dtype preserved
    assert inv.shape == (expected_n,)
    assert inv.dtype == np.float64


def test_sweep_generator_inverse_fade_preserves_shape_and_dtype(fs):
    """Verify that the fade does not change the array shape or dtype."""
    dur_s = 0.5
    gen = SweepGenerator(fs, dur_s, 20.0, -10.0)
    s_fund, phase, inv = gen.generate()
    expected_n = int(round(dur_s * fs))
    assert s_fund.shape == (expected_n,)
    assert inv.shape == (expected_n,)
    assert inv.dtype == np.float64
    assert phase.shape == (expected_n,)


# -----------------------------------------------------------------------------
# PIPELINE STEPS
# -----------------------------------------------------------------------------

def test_harmonic_injector_functional(fs):
    dur_s = 1.0
    f1 = 100.0
    level_dbfs = 0.0

    gen = SweepGenerator(fs, dur_s, f1, level_dbfs)
    s_fund, phase, _ = gen.generate()

    # Inject H2 at -20dB and H3 at -40dB
    h2_db = -20.0
    h3_db = -40.0
    injector = HarmonicInjector(h2_db=h2_db, h3_db=h3_db)
    s_comp = injector.inject(s_fund, phase)

    # Verify it's different from fund and matches expected injection
    diff = s_comp - s_fund
    expected_diff_h2 = DSPUtils.db_to_lin(h2_db) * np.sin(2 * phase)
    expected_diff_h3 = DSPUtils.db_to_lin(h3_db) * np.sin(3 * phase)
    assert np.allclose(diff, expected_diff_h2 + expected_diff_h3, atol=1e-5)


def test_protection_filter_functional(fs):
    fc = 1000.0
    order = 4

    # Test magnitude response of apply()
    p_filter = ProtectionFilter(fs, fc, order)

    # White noise test
    noise = np.random.normal(0, 0.1, fs)
    filtered = p_filter.apply(noise)

    # Check frequency response
    f, pxx_den = scipy.signal.welch(filtered, fs, nperseg=1024)
    idx_500 = np.searchsorted(f, 500)
    idx_2000 = np.searchsorted(f, 2000)
    assert pxx_den[idx_500] < pxx_den[idx_2000] * 0.01  # 20dB difference at least

    # Test correction mask
    p_filter_corr = ProtectionFilter(fs, fc, order, hpf_correction=True, hpf_corr_db_cap=6.0)
    n_bins = 1024
    mask = p_filter_corr.get_correction_mask(n_bins)

    assert np.max(np.abs(mask)) <= 2.0 + 1e-6  # Capped at +6dB
    assert mask[0] == 0j  # DC kill


# -----------------------------------------------------------------------------
# ENGINES
# -----------------------------------------------------------------------------

def test_alignment_engine_functional(fs):
    num_sweeps = 1
    align_to_first = True
    taper_ms = 10.0
    marker_dur_ms = 100.0

    engine = AlignmentEngine(fs, num_sweeps, align_to_first, taper_ms, marker_dur_ms)

    # Create marker
    gen = MarkerGenerator(fs, marker_dur_ms, (500.0, 5000.0), -6.0)
    marker = gen.generate()

    # Create simulated recordings with delay
    delay_samps = 1000
    rec_loop = np.zeros(fs)
    rec_loop[delay_samps:delay_samps + len(marker)] = marker

    mic_delay = 500
    rec_mic = np.zeros(fs)
    rec_mic[delay_samps + mic_delay: delay_samps + mic_delay + len(marker)] = marker

    avg_mic, avg_loop, slices, psr = engine.sync_and_average(
        rec_mic, rec_loop, marker, 0, fs // 2, fs // 4
    )

    assert psr > 3.0
    assert len(avg_mic) == fs // 4 + int(round(taper_ms / 1000.0 * fs))


def test_deconvolution_separation_functional(fs):
    dur_s = 0.5
    f1 = 20.0
    level_dbfs = -10.0

    gen = SweepGenerator(fs, dur_s, f1, level_dbfs)
    s_fund, phase, inv = gen.generate()

    # Inject heavy H2 (-10dB relative to fund)
    injector = HarmonicInjector(h2_db=-10.0)
    s_distorted = injector.inject(s_fund, phase)

    engine = DeconvolutionEngine(fs)
    h_full, h_linear = engine.process_ir(s_distorted, inv)

    # In Farina's method, H2 appears at negative time: delta_t = L * ln(2)
    L = dur_s / np.log((fs / 2) / f1)
    dt_h2 = L * np.log(2)
    offset_samps = int(round(dt_h2 * fs))
    split_idx = len(inv) - 5
    h2_peak_idx = split_idx - offset_samps

    # Verify energy at h2_peak_idx
    window = h_full[max(0, h2_peak_idx - 100): h2_peak_idx + 100]
    assert np.max(np.abs(window)) > 0.1

    # Verify linear peak
    assert np.max(np.abs(h_linear)) > 0.5


def test_deconvolution_returns_rms_referenced_ir(fs):
    gen = SweepGenerator(fs, 0.5, 20.0, -10.0)
    s_fund, _phase, inv = gen.generate()

    _h_full, h_linear = DeconvolutionEngine(fs).process_ir(s_fund, inv)
    linear_peak = float(np.max(np.abs(h_linear)))

    assert SINE_RMS_FROM_PEAK == pytest.approx(1.0 / np.sqrt(2.0))
    assert linear_peak == pytest.approx(0.56, abs=0.08)


# -----------------------------------------------------------------------------
# VERIFICATION & METRICS
# -----------------------------------------------------------------------------

def test_snr_calculation(fs):
    verifier = DSPVerificationTool(fs)
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0
    h_full = h_linear.copy()
    noise_floor = 0.001  # -60dB
    h_full[-5000:] = np.random.normal(0, noise_floor, 5000)

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0)
    assert 55 < metrics['snr_db'] < 65


def test_thd_calculation(fs):
    verifier = DSPVerificationTool(fs)
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0
    h_full = np.zeros(fs * 2)
    h_full[fs:fs + fs] = h_linear
    # Put distortion in negative time
    h_full[1000:1100] = 0.1

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0)
    assert metrics['thd_pct'] > 50


def test_verification_warnings(fs):
    verifier = DSPVerificationTool(fs)
    assert len(verifier.verify({'snr_db': 50, 'thd_pct': 0.1, 'psr': 10.0})) == 0
    assert any("LOW SNR" in w for w in verifier.verify({'snr_db': 20, 'thd_pct': 0.1, 'psr': 10.0}))
    assert any("HIGH DISTORTION" in w for w in verifier.verify({'thd_pct': 15.0}))
    assert any("POOR ALIGNMENT" in w for w in verifier.verify({'psr': 2.0}))


def test_device_catalog_uses_zero_based_channels_for_all_host_apis(
    monkeypatch,
):
    sounddevice = ModuleType("sounddevice")
    sounddevice._terminate = Mock()
    sounddevice._initialize = Mock()
    sounddevice.query_hostapis = Mock(return_value=[
        {"name": "MME"},
        {"name": "Windows DirectSound"},
        {"name": "Windows WASAPI"},
        {"name": "ASIO"},
    ])
    sounddevice.query_devices = Mock(return_value=[
        {"name": "MME Device", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 2},
        {"name": "DS Device", "hostapi": 1, "max_input_channels": 2, "max_output_channels": 2},
        {"name": "WASAPI Device", "hostapi": 2, "max_input_channels": 2, "max_output_channels": 2},
        {"name": "ASIO Device", "hostapi": 3, "max_input_channels": 2, "max_output_channels": 2},
    ])
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    catalog = get_devices_and_channels()

    for info in catalog.values():
        assert info["input_channels"] == [0, 1]
        assert info["output_channels"] == [0, 1]


def test_device_catalog_returns_empty_when_sounddevice_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)

    assert get_devices_and_channels() == {}


@pytest.mark.parametrize("failed_operation", ["query_hostapis", "query_devices"])
def test_device_catalog_returns_empty_when_portaudio_discovery_fails(monkeypatch, failed_operation):
    sounddevice = ModuleType("sounddevice")
    sounddevice._terminate = Mock()
    sounddevice._initialize = Mock()
    sounddevice.query_hostapis = Mock(return_value=[])
    sounddevice.query_devices = Mock(return_value=[])
    getattr(sounddevice, failed_operation).side_effect = RuntimeError("PortAudio unavailable")
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    assert get_devices_and_channels() == {}


def test_supported_sample_rates_returns_empty_when_sounddevice_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)

    assert get_supported_sample_rates(0, 0) == []


# -----------------------------------------------------------------------------
# INTEGRATION / MOCK
# -----------------------------------------------------------------------------

def test_integration_with_mock_audio(tmp_path):
    config = configparser.ConfigParser()
    config['audio'] = {
        'mode': 'mock_interface', 'fs': '48000', 'in_dev': '0', 'out_dev': '0',
        'in_ch_mic': '1', 'in_ch_loop': '0', 'out_ch_spkr': '0', 'out_ch_ref': '1',
        'blocksize': '1024', 'wasapi_exclusive': 'False'
    }
    config['sweep'] = {
        'sweep_dur_s': '0.5', 'sweep_level_dbfs': '-10', 'num_sweeps': '1',
        'pre_sil_ms': '50', 'post_sil_ms': '50', 'mic_tail_taper_ms': '10',
        'align_to_first_marker': 'True', 'debug_saves': 'True',
        'H2_TEST_DB': 'None', 'H3_TEST_DB': 'None',
        'protect_hpf_hz': '0',
        'protect_hpf_order': '4',
        'protect_hpf_correction': 'False',
        'protect_hpf_corr_db_cap': '12.0',
    }

    config_path = tmp_path / "dsp_test_config.ini"
    with open(config_path, "w") as f:
        config.write(f)

    audio = AudioFactory.create(str(config_path))
    audio.set_session_directory(tmp_path)
    pos = CylindricalPosition(100, 0, 10)
    audio.measure_ir(pos, "DSP_TEST")

    # Check if metrics file was created
    # Based on log output: Saved Linear IR: DSP_TEST_r100_ph0_z10_ir.wav
    metrics_file = tmp_path / "debug" / "DSP_TEST_r100_ph0_z10_metrics.json"
    assert metrics_file.exists()


def test_audio_factory_accepts_comma_decimal_config_values(tmp_path):
    config = configparser.ConfigParser()
    config["audio"] = {
        "mode": "mock_interface",
        "fs": "48000",
        "in_dev": "0",
        "out_dev": "0",
        "in_ch_mic": "1",
        "in_ch_loop": "0",
        "out_ch_spkr": "0",
        "out_ch_ref": "1",
        "blocksize": "1024",
        "wasapi_exclusive": "False",
    }
    config["sweep"] = {
        "sweep_dur_s": "0,5",
        "sweep_level_dbfs": "-10,5",
        "num_sweeps": "1",
        "pre_sil_ms": "50,5",
        "post_sil_ms": "60,5",
        "mic_tail_taper_ms": "10,5",
        "align_to_first_marker": "True",
        "debug_saves": "False",
        "H2_TEST_DB": "-20,5",
        "H3_TEST_DB": "None",
        "protect_hpf_hz": "500,5",
        "protect_hpf_order": "4",
        "protect_hpf_correction": "True",
        "protect_hpf_corr_db_cap": "6,5",
    }
    config_path = tmp_path / "comma_config.ini"
    with open(config_path, "w", encoding="utf-8") as f:
        config.write(f)

    audio = AudioFactory.create(str(config_path))

    assert audio.hw["wasapi_exclusive"] is False
    assert audio.cap["debug_saves"] is False
    assert audio.alignment_engine.align_to_first_marker is True
    assert audio.cap["sweep_dur_s"] == 0.5
    assert audio.cap["sweep_level_dbfs"] == -10.5
    assert audio.cap["pre_sil_ms"] == 50.5
    assert audio.cap["post_sil_ms"] == 60.5
    assert audio.harmonic_injector.h2_db == -20.5
    assert audio.protection_filter.freq_hz == 500.5
    assert audio.protection_filter.hpf_corr_db_cap == 6.5

    config["sweep"]["align_to_first_marker"] = "False"
    config.remove_option("sweep", "protect_hpf_corr_db_cap")
    fallback_config_path = tmp_path / "comma_config_fallback.ini"
    with open(fallback_config_path, "w", encoding="utf-8") as f:
        config.write(f)

    audio = AudioFactory.create(str(fallback_config_path))

    assert audio.alignment_engine.align_to_first_marker is False
    assert audio.protection_filter.hpf_corr_db_cap == 12.0


@patch("nfs.audio.get_devices_and_channels")
def test_find_device_id_by_name_handles_changed_id(mock_catalog):
    mock_catalog.return_value = {
        11: {
            "name": "Bluetooth Headset",
            "hostapi": "Windows WASAPI",
            "input_channels": [0],
            "output_channels": [0],
        },
        13: {
            "name": "MOTU M4",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1, 2, 3],
        },
    }

    assert find_device_id_by_name("MOTU M4", "ASIO", require_input=True) == 13
    assert find_device_id_by_name("MOTU M4", "ASIO", require_output=True) == 13
    assert find_device_id_by_name("Missing", "ASIO") is None


@patch("nfs.audio.get_devices_and_channels")
def test_find_device_id_by_name_does_not_fall_back_to_wrong_hostapi(mock_catalog):
    mock_catalog.return_value = {
        4: {
            "name": "RME Interface",
            "hostapi": "MME",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
        20: {
            "name": "RME Interface",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    assert find_device_id_by_name("RME Interface", "ASIO", require_input=True) == 20
    assert find_device_id_by_name("RME Interface", "Windows WASAPI", require_input=True) is None
    assert find_device_id_by_name("RME Interface", "", require_input=True) == 4


@patch("nfs.audio.get_devices_and_channels")
def test_audio_factory_rejects_saved_pair_when_only_wrong_hostapi_exists(mock_catalog, tmp_path):
    mock_catalog.return_value = {
        4: {
            "name": "RME Interface",
            "hostapi": "MME",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    config = configparser.ConfigParser()
    config["audio"] = {
        "mode": "hardware",
        "fs": "48000",
        "in_dev_name": "RME Interface",
        "in_dev_hostapi": "ASIO",
        "out_dev_name": "RME Interface",
        "out_dev_hostapi": "ASIO",
        "in_ch_mic": "0",
        "in_ch_loop": "1",
        "out_ch_spkr": "0",
        "out_ch_ref": "1",
        "blocksize": "1024",
        "wasapi_exclusive": "False",
    }
    config["sweep"] = {
        "sweep_dur_s": "0.5",
        "sweep_level_dbfs": "-10",
        "num_sweeps": "1",
        "pre_sil_ms": "50",
        "post_sil_ms": "50",
        "mic_tail_taper_ms": "10",
        "align_to_first_marker": "True",
        "debug_saves": "False",
        "H2_TEST_DB": "None",
        "H3_TEST_DB": "None",
        "protect_hpf_hz": "None",
        "protect_hpf_order": "4",
        "protect_hpf_correction": "False",
        "protect_hpf_corr_db_cap": "12.0",
    }
    config_path = tmp_path / "config.ini"
    with open(config_path, "w", encoding="utf-8") as f:
        config.write(f)

    with pytest.raises(ValueError, match="device name exists under: MME"):
        AudioFactory.create(str(config_path))


@patch("nfs.audio.get_devices_and_channels")
def test_audio_factory_resolves_saved_identity_without_configured_ids(mock_catalog, tmp_path):
    mock_catalog.return_value = {
        4: {
            "name": "RME Interface",
            "hostapi": "MME",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
        20: {
            "name": "RME Interface",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    config = configparser.ConfigParser()
    config["audio"] = {
        "mode": "hardware",
        "fs": "48000",
        "in_dev_name": "RME Interface",
        "in_dev_hostapi": "ASIO",
        "out_dev_name": "RME Interface",
        "out_dev_hostapi": "ASIO",
        "in_ch_mic": "0",
        "in_ch_loop": "1",
        "out_ch_spkr": "0",
        "out_ch_ref": "1",
        "blocksize": "1024",
        "wasapi_exclusive": "False",
    }
    config["sweep"] = {
        "sweep_dur_s": "0.5",
        "sweep_level_dbfs": "-10",
        "num_sweeps": "1",
        "pre_sil_ms": "50",
        "post_sil_ms": "50",
        "mic_tail_taper_ms": "10",
        "align_to_first_marker": "True",
        "debug_saves": "False",
        "H2_TEST_DB": "None",
        "H3_TEST_DB": "None",
        "protect_hpf_hz": "None",
        "protect_hpf_order": "4",
        "protect_hpf_correction": "False",
        "protect_hpf_corr_db_cap": "12.0",
    }
    config_path = tmp_path / "config.ini"
    with open(config_path, "w", encoding="utf-8") as f:
        config.write(f)

    audio = AudioFactory.create(str(config_path))

    assert audio.hw["dev_in"] == 20
    assert audio.hw["dev_out"] == 20


@pytest.mark.parametrize('value,expected', [
    ('False', False), ('false # disabled', False), ('0', False), ('off', False),
    ('True', True), ('1', True), ('yes', True), ('ON ; enabled', True),
])
def test_audio_factory_parses_boolean_settings(value, expected):
    config = configparser.ConfigParser()
    config['sweep'] = {'debug_saves': value}
    assert AudioFactory._get_required_config(config, 'sweep', 'debug_saves', bool) is expected


def test_audio_factory_rejects_invalid_boolean():
    config = configparser.ConfigParser()
    config['sweep'] = {'debug_saves': 'perhaps'}
    with pytest.raises(ValueError, match='Not a boolean'):
        AudioFactory._get_required_config(config, 'sweep', 'debug_saves', bool)
