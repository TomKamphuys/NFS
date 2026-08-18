import configparser
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from nfs.audio_diagnostic import (
    CallbackTrace,
    CoarseProgress,
    DiagnosticRequest,
    TrialResult,
    _backend_series,
    _finish_direct_analysis,
    _loopback_check,
    _temporary_config,
    _trace_metrics,
    analyze_capture,
    analyze_sine_capture,
    generate_probe,
    read_request,
    request_from_config,
    run_diagnostic,
    run_diagnostic_group,
    write_request,
)


def _write_config(path: Path) -> None:
    config = configparser.ConfigParser()
    config["audio"] = {
        "mode": "hardware",
        "in_dev": "4",
        "out_dev": "4",
        "in_dev_name": "Test ASIO",
        "in_dev_hostapi": "ASIO",
        "out_dev_name": "Test ASIO",
        "out_dev_hostapi": "ASIO",
        "in_ch_mic": "0",
        "in_ch_loop": "1",
        "out_ch_spkr": "0",
        "out_ch_ref": "1",
        "fs": "48000",
        "blocksize": "0",
        "wasapi_exclusive": "True",
    }
    config["sweep"] = {
        "sweep_dur_s": "1",
        "sweep_level_dbfs": "-20",
        "num_sweeps": "1",
        "pre_sil_ms": "100",
        "post_sil_ms": "100",
        "mic_tail_taper_ms": "10",
        "align_to_first_marker": "True",
        "debug_saves": "False",
        "H2_TEST_DB": "None",
        "H3_TEST_DB": "None",
        "protect_hpf_hz": "0",
        "protect_hpf_order": "4",
        "protect_hpf_correction": "False",
        "protect_hpf_corr_db_cap": "12",
    }
    with path.open("w", encoding="utf-8") as handle:
        config.write(handle)


def test_request_uses_saved_electrical_loopback(tmp_path):
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    request = request_from_config(config_path, tmp_path / "results")

    assert request.input_device_name == "Test ASIO"
    assert request.output_device_name == "Test ASIO"
    assert request.input_channel == 1
    assert request.output_channel == 1
    assert request.sample_rate == 48000
    assert request.blocksize == 0

    saved = write_request(request, tmp_path / "request.json")
    assert read_request(saved) == request


def test_temporary_backend_config_does_not_modify_source(tmp_path):
    source = tmp_path / "config.ini"
    target = tmp_path / "diagnostic.ini"
    _write_config(source)
    before = source.read_text(encoding="utf-8")
    request = DiagnosticRequest(
        config_path=str(source),
        input_device_name="RME ASIO",
        input_hostapi="ASIO",
        output_device_name="RME ASIO",
        output_hostapi="ASIO",
        input_channel=3,
        output_channel=5,
        sample_rate=96000,
        blocksize=512,
        output_root=str(tmp_path),
    )
    catalog = {
        8: {"input_channels": list(range(8)), "output_channels": []},
        9: {"input_channels": [], "output_channels": list(range(8))},
    }

    _temporary_config(source, target, 96000, 512, request, 8, 9, catalog)

    assert source.read_text(encoding="utf-8") == before
    parser = configparser.ConfigParser()
    parser.read(target)
    assert parser.get("audio", "in_dev_name") == "RME ASIO"
    assert parser.getint("audio", "in_ch_loop") == 3
    assert parser.getint("audio", "out_ch_ref") == 5
    assert parser.getint("audio", "fs") == 96000
    assert parser.getint("audio", "blocksize") == 512


def test_analyzer_accepts_clean_delayed_capture():
    sample_rate = 48000
    expected = generate_probe(sample_rate, 3.0, seed=123)
    delay = 137
    captured = np.concatenate([np.zeros(delay, dtype=np.float32), expected])[: len(expected)]

    result = analyze_capture(expected, captured, sample_rate, startup_seconds=0.2)

    assert result["passed"] is True
    assert result["global_lag_frames"] == delay
    assert result["lag_jump_frames"] == []
    assert result["window_correlation_median"] > 0.99


def test_analyzer_detects_dropped_samples():
    sample_rate = 48000
    expected = generate_probe(sample_rate, 4.0, seed=456)
    delay = 91
    drop_at = 80000
    drop_count = 256
    captured = np.concatenate(
        [
            np.zeros(delay, dtype=np.float32),
            expected[:drop_at],
            expected[drop_at + drop_count :],
        ]
    )
    captured = np.pad(captured, (0, max(0, len(expected) - len(captured))))[: len(expected)]

    result = analyze_capture(expected, captured, sample_rate, startup_seconds=0.2)

    assert result["passed"] is False
    assert abs(result["estimated_net_skipped_frames"]) == drop_count
    assert result["lag_jump_frames"]


def test_analyzer_reports_late_start_but_judges_steady_audio():
    sample_rate = 48000
    expected = generate_probe(sample_rate, 8.0, seed=789)
    captured = expected.copy()
    captured[: int(2.7 * sample_rate)] = 0.0

    result = analyze_capture(expected, captured, sample_rate)

    assert result["passed"] is True
    assert result["startup_delay_warning"] is True
    assert result["signal_onset_ms"] == 2700.0
    assert result["steady_analysis_start_seconds"] >= 2.9
    assert result["steady_analyzed_seconds"] > 5.0
    assert result["window_correlation_p05"] > 0.99


def test_sine_analyzer_accepts_clean_delayed_tone():
    sample_rate = 48000
    frequency = 997.0
    frames = np.arange(sample_rate * 4, dtype=np.float64)
    expected = (0.03 * np.sin(2.0 * np.pi * frequency * frames / sample_rate)).astype(np.float32)
    captured = np.concatenate([np.zeros(137, dtype=np.float32), expected])[: len(expected)]

    result = analyze_sine_capture(expected, captured, sample_rate, frequency=frequency, startup_seconds=0.2)

    assert result["passed"] is True
    assert result["sine_fit_residual_median"] < 0.01


def test_sine_analyzer_detects_phase_discontinuity():
    sample_rate = 48000
    frequency = 997.0
    frames = np.arange(sample_rate * 4, dtype=np.float64)
    captured = 0.03 * np.sin(2.0 * np.pi * frequency * frames / sample_rate)
    captured[sample_rate * 2 :] = 0.03 * np.sin(
        2.0 * np.pi * frequency * frames[sample_rate * 2 :] / sample_rate + 1.0
    )

    result = analyze_sine_capture(
        captured.astype(np.float32), captured.astype(np.float32), sample_rate,
        frequency=frequency, startup_seconds=0.2,
    )

    assert result["passed"] is False
    assert result["phase_event_window_indices"]


def test_sine_analyzer_reports_late_start_separately():
    sample_rate = 48000
    frequency = 997.0
    frames = np.arange(sample_rate * 8, dtype=np.float64)
    expected = (0.03 * np.sin(2.0 * np.pi * frequency * frames / sample_rate)).astype(np.float32)
    captured = expected.copy()
    captured[: int(2.7 * sample_rate)] = 0.0

    result = analyze_sine_capture(expected, captured, sample_rate, frequency=frequency)

    assert result["passed"] is True
    assert result["startup_delay_warning"] is True
    assert result["signal_onset_ms"] == 2700.0
    assert result["steady_analyzed_seconds"] > 5.0


def test_trace_metrics_reports_effective_callback_rate():
    sample_rate = 48000
    frames = [480] * 11
    trace = CallbackTrace(
        monotonic_ns=[1_000_000_000 + index * 5_000_000 for index in range(11)],
        frames=frames,
    )

    metrics = _trace_metrics(trace, sample_rate)

    assert metrics["effective_callback_frame_rate"] == 96000
    assert metrics["effective_to_requested_rate_ratio"] == 2.0


def test_direct_analysis_marks_wrong_callback_rate_as_failure():
    sample_rate = 48000
    reference = generate_probe(sample_rate, 3.0, seed=909)
    result = TrialResult(
        name="direct_test",
        engine="direct",
        scenario="test",
        sample_rate=sample_rate,
        requested_blocksize=480,
        callback_frames={"480": 11},
        callback_statuses={},
        elapsed_seconds=3.0,
        expected_seconds=3.0,
        analysis={},
    )
    trace = CallbackTrace(
        monotonic_ns=[1_000_000_000 + index * 5_000_000 for index in range(11)],
        frames=[480] * 11,
    )

    _finish_direct_analysis((result, reference, reference.copy(), trace))

    assert result.analysis["passed"] is False
    assert result.analysis["callback_rate_failure"] is True


def test_progress_is_coarse_and_only_printed_on_bucket_change(capsys):
    progress = CoarseProgress(100, step_percent=10)
    for _ in range(9):
        progress.complete("trial")
    first = capsys.readouterr().out
    assert first.count("Audio diagnostic:") == 1

    progress.complete("trial ten")
    second = capsys.readouterr().out
    assert "10%" in second


def test_loopback_check_prioritizes_runaway_driver_timing():
    check, message, problem_kind = _loopback_check(
        np.zeros(48000 * 3, dtype=np.float32),
        48000,
        {
            "effective_callback_frame_rate": 20_000_000.0,
            "effective_to_requested_rate_ratio": 416.6667,
        },
    )

    assert check["passed"] is False
    assert problem_kind == "driver_timing_failure"
    assert "20,000,000 frames/second" in message
    assert "electrical loopback level cannot be assessed" in message.lower()


def test_production_series_reuses_one_audio_object(tmp_path, monkeypatch):
    source = tmp_path / "config.ini"
    _write_config(source)
    request = request_from_config(source, tmp_path)
    catalog = {
        4: {
            "name": "Test ASIO",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        }
    }
    import nfs.audio as audio_module
    import nfs.audio_diagnostic as diagnostic_module

    factory_calls = []
    play_calls = []
    time_info = SimpleNamespace(inputBufferAdcTime=0.0, currentTime=0.0, outputBufferDacTime=0.0)

    class FakeNativeStream:
        def __init__(self, *args, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            indata = np.tile(np.linspace(-0.1, 0.1, 64, dtype=np.float32)[:, None], (1, 2))
            outdata = np.zeros((64, 2), dtype=np.float32)
            self.callback(indata, outdata, 64, time_info, None)

        def stop(self):
            pass

        def close(self):
            pass

    class FakeAudio:
        def __init__(self):
            self.stream = None

        def play_sine(self, frequency, level, duration):
            play_calls.append(duration)

            def callback(indata, outdata, frames, _time_info, _status):
                outdata[:] = indata

            stream = audio_module.sd.Stream(callback=callback)
            if duration is None:
                self.stream = stream
                stream.start()
            else:
                stream.start()
                stream.stop()
                stream.close()

        def stop_sine(self):
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None

    def create(path):
        factory_calls.append(path)
        return FakeAudio()

    monkeypatch.setattr(audio_module.AudioFactory, "create", create)
    monkeypatch.setattr(audio_module.sd, "Stream", FakeNativeStream)
    monkeypatch.setattr(diagnostic_module.time, "sleep", lambda _duration: None)

    payloads = _backend_series(
        request,
        4,
        4,
        catalog,
        sample_rate=48000,
        blocksize=512,
        run_dir=tmp_path,
    )

    assert len(factory_calls) == 1
    assert play_calls == [None, 4.0, 4.0, 4.0]
    assert len(payloads) == 4


def test_orchestrator_runs_backend_first_in_separate_groups(tmp_path, monkeypatch):
    source = tmp_path / "config.ini"
    _write_config(source)
    request = request_from_config(source, tmp_path / "results")
    request_path = write_request(request, tmp_path / "request.json")
    groups = []

    def fake_run(command, check):
        group = command[command.index("--audio-diagnostic-group") + 1]
        run_dir = Path(command[command.index("--audio-diagnostic-run-dir") + 1])
        groups.append(group)
        (run_dir / f"group_{group}.json").write_text(
            json.dumps({"group": group, "results": [], "setup_problem": None}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("nfs.audio_diagnostic.subprocess.run", fake_run)

    archive = run_diagnostic(request_path, launcher_command=["diagnostic-executable"])

    assert groups == ["backend", "direct", "transitions"]
    assert archive.exists()


def test_orchestrator_runs_only_direct_smoke_after_driver_timing_failure(tmp_path, monkeypatch):
    source = tmp_path / "config.ini"
    _write_config(source)
    request = request_from_config(source, tmp_path / "results")
    request_path = write_request(request, tmp_path / "request.json")
    groups = []

    def fake_run(command, check):
        group = command[command.index("--audio-diagnostic-group") + 1]
        run_dir = Path(command[command.index("--audio-diagnostic-run-dir") + 1])
        groups.append(group)
        payload = {"group": group, "results": []}
        if group == "backend":
            payload.update(
                setup_problem="Driver callbacks are unpaced.",
                problem_kind="driver_timing_failure",
            )
        (run_dir / f"group_{group}.json").write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("nfs.audio_diagnostic.subprocess.run", fake_run)

    archive = run_diagnostic(request_path, launcher_command=["diagnostic-executable"])
    run_dir = archive.with_name(archive.name.removesuffix("_SEND_THIS_FILE.zip"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert groups == ["backend", "direct_smoke"]
    assert summary["status"] == "driver_timing_failed"
    assert summary["problem_kind"] == "driver_timing_failure"


def test_transition_group_defers_analysis_until_stream_sequence_is_closed(tmp_path, monkeypatch):
    source = tmp_path / "config.ini"
    _write_config(source)
    request_path = write_request(request_from_config(source, tmp_path), tmp_path / "request.json")
    events = []
    import nfs.audio_diagnostic as diagnostic_module

    monkeypatch.setattr(diagnostic_module, "_resolve_devices", lambda request: (4, 4, {}))
    monkeypatch.setattr(diagnostic_module, "_supported_rates", lambda *args: [48000, 96000])
    monkeypatch.setattr(
        diagnostic_module,
        "generate_probe",
        lambda sample_rate, duration, seed: np.zeros(64, dtype=np.float32),
    )
    monkeypatch.setattr(diagnostic_module.time, "sleep", lambda seconds: events.append(f"wait:{seconds}"))
    monkeypatch.setattr(diagnostic_module, "_write_trial_artifacts", lambda *args: None)

    def fake_trial(*args, **kwargs):
        assert kwargs["defer_analysis"] is True
        name = kwargs["name"]
        events.append(f"stream:{name}")
        result = TrialResult(
            name=name,
            engine="direct",
            scenario=kwargs["scenario"],
            sample_rate=kwargs["sample_rate"],
            requested_blocksize=kwargs["blocksize"],
            callback_frames={"64": 1},
            callback_statuses={},
            elapsed_seconds=0.1,
            expected_seconds=0.1,
            analysis={"analysis_deferred": True},
        )
        return result, np.zeros(64), np.zeros(64), CallbackTrace(monotonic_ns=[1, 2], frames=[64, 64])

    def fake_finish(payload):
        events.append(f"analyze:{payload[0].name}")
        payload[0].analysis = {"passed": True}
        return payload

    monkeypatch.setattr(diagnostic_module, "_direct_trial", fake_trial)
    monkeypatch.setattr(diagnostic_module, "_finish_direct_analysis", fake_finish)

    run_diagnostic_group(request_path, tmp_path, "transitions", progress_total=9)

    first_analysis = next(index for index, event in enumerate(events) if event.startswith("analyze:"))
    assert [event.split(":", 1)[0] for event in events[:first_analysis]] == [
        "stream",
        "wait",
        "stream",
        "stream",
    ]
