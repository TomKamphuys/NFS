import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt.qt_compat import QApplication, QLabel
from harmonic_drive_qt import directivity_viewer
from harmonic_drive_qt.settings_dialog import SettingsDialog


def _app():
    return QApplication.instance() or QApplication([])


def _assert_frequency_x_axes(canvas, horizontal_count, vertical_count, frequency_count):
    figure = canvas.layout().itemAt(0).widget().figure
    plot_axes = [
        axis for axis in figure.axes
        if axis.get_ylabel() in {"Cylindrical θ (degrees)", "Elevation angle (degrees)"}
    ]
    horizontal, vertical = plot_axes

    assert horizontal.get_xlabel() == "Frequency (Hz)"
    assert vertical.get_xlabel() == "Frequency (Hz)"
    assert horizontal.get_ylabel() == "Cylindrical θ (degrees)"
    assert vertical.get_ylabel() == "Elevation angle (degrees)"
    for axis, angle_count, expected_label in (
        (horizontal, horizontal_count, "Cylindrical θ (degrees)"),
        (vertical, vertical_count, "Elevation angle (degrees)"),
    ):
        mesh = axis.collections[0]
        assert mesh.get_array().shape == (angle_count, frequency_count)
        assert axis.get_xscale() == "log"
        assert axis.get_xlim() == pytest.approx((100, 20000))
        assert axis.get_ylim() == pytest.approx((-180, 180))
        assert np.allclose(np.max(mesh.get_array(), axis=0), 0)
        assert axis.get_ylabel() == expected_label


def test_spherical_harmonic_canvas_puts_frequency_on_x(monkeypatch):
    _app()
    angle_count, elevation_count, bin_count = 4, 3, 2401
    freq_grid = np.linspace(100, 20000, 5)
    mag_h = np.tile(np.linspace(0.2, 1.0, angle_count)[:, None], (1, bin_count))
    mag_v = np.tile(np.linspace(0.3, 1.0, elevation_count)[:, None], (1, bin_count))
    monkeypatch.setattr(
        directivity_viewer,
        "_build_directivity_plots",
        lambda model: (
            mag_h,
            np.linspace(-180, 180, angle_count, endpoint=False),
            mag_v,
            np.linspace(-180, 180, elevation_count, endpoint=False),
            freq_grid,
            angle_count,
            elevation_count,
        ),
    )

    canvas = directivity_viewer._DirectivityCanvas(SimpleNamespace(_freq_bins=bin_count))

    _assert_frequency_x_axes(canvas, angle_count, elevation_count, len(freq_grid))


def test_generic_canvas_puts_frequency_on_x():
    _app()
    angle_count, elevation_count, frequency_count = 4, 3, 5
    angles = np.linspace(-180, 180, angle_count, endpoint=False)
    elevations = np.linspace(-180, 180, elevation_count, endpoint=False)
    mag_h = np.tile(np.linspace(0.2, 1.0, angle_count)[:, None], (1, frequency_count))
    mag_v = np.tile(np.linspace(0.3, 1.0, elevation_count)[:, None], (1, frequency_count))

    canvas = directivity_viewer._GenericDirectivityCanvas(
        mag_h, angles, mag_v, elevations, np.geomspace(100, 20000, frequency_count),
        angle_count, elevation_count,
    )

    _assert_frequency_x_axes(canvas, angle_count, elevation_count, frequency_count)


def test_discrete_piston_preview_uses_sparse_frequencies_and_full_circle(monkeypatch):
    from nfs.datatypes import CylindricalPosition
    from nfs.utils.spatial import DiscretePistonSpeakerModel

    original_sum = getattr(
        DiscretePistonSpeakerModel, "_compute_green_function_response", None
    )
    calls = []

    def record_sum(model, position, frequencies, speaker_origin=None):
        calls.append((position, np.asarray(frequencies).copy()))
        if original_sum is None:
            return np.ones(len(frequencies), dtype=np.complex128)
        return original_sum(model, position, frequencies, speaker_origin)

    def reject_full_response(*args, **kwargs):
        pytest.fail("directivity preview must not compute full FFT responses")

    monkeypatch.setattr(
        DiscretePistonSpeakerModel,
        "_compute_green_function_response",
        record_sum,
        raising=False,
    )
    monkeypatch.setattr(
        DiscretePistonSpeakerModel, "compute_frequency_response", reject_full_response
    )

    mag_h, angles_deg, mag_v, elev_angles_deg = (
        directivity_viewer._build_discrete_piston_directivity()
    )

    assert len(calls) == 2 * 361
    assert all(frequencies.shape == (200,) for _, frequencies in calls)
    np.testing.assert_array_equal(angles_deg[[0, 180, -1]], [-180.0, 0.0, 180.0])
    np.testing.assert_array_equal(elev_angles_deg[[0, 180, -1]], [-180.0, 0.0, 180.0])
    np.testing.assert_allclose(mag_h[0], mag_h[180], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(mag_h[-1], mag_h[180], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(mag_v[0], mag_v[180], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(mag_v[-1], mag_v[180], rtol=1e-12, atol=1e-12)

    horizontal_positions = [position for position, _ in calls[:361]]
    vertical_positions = [position for position, _ in calls[361:]]
    assert isinstance(horizontal_positions[0], CylindricalPosition)
    assert horizontal_positions[0].t() == pytest.approx(-180.0)
    assert horizontal_positions[-1].t() == pytest.approx(180.0)
    assert vertical_positions[315].t() == pytest.approx(180.0)
    assert vertical_positions[45].t() == pytest.approx(180.0)
    assert vertical_positions[225].t() == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("selection", "expected_canvas"),
    [
        ("none", "generic"),
        ("spherical_harmonic", "spherical_harmonic"),
        ("discrete_piston", "generic"),
    ],
)
def test_viewer_uses_explicit_unsaved_model_selection(
    tmp_path, monkeypatch, selection, expected_canvas
):
    _app()
    config_file = tmp_path / "config.ini"
    original_config = "[sweep]\nspatial_model = none\n"
    config_file.write_text(original_config, encoding="utf-8")
    calls = []

    class TestCanvas(QLabel):
        def __init__(self, *args, **kwargs):
            super().__init__()
            calls.append("spherical_harmonic" if len(args) == 1 else "generic")

    monkeypatch.setattr(directivity_viewer, "_DirectivityCanvas", TestCanvas)
    monkeypatch.setattr(directivity_viewer, "_GenericDirectivityCanvas", TestCanvas)
    monkeypatch.setattr(
        directivity_viewer,
        "_build_discrete_piston_directivity",
        lambda: (
            np.ones((2, 3)), np.array([-90, 90]),
            np.ones((2, 3)), np.array([-90, 90]),
        ),
    )
    from nfs.utils import spatial
    monkeypatch.setattr(
        spatial,
        "SphericalHarmonicSpeakerModel",
        lambda: SimpleNamespace(model="sh"),
    )

    window = directivity_viewer.DirectivityViewerWindow(
        str(config_file), spatial_model=selection
    )

    assert calls == [expected_canvas]
    assert config_file.read_text(encoding="utf-8") == original_config
    window.close()


def test_settings_preview_passes_unsaved_selection_without_saving(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    original_config = (
        "[audio]\nmode = mock_interface\n"
        "[sweep]\nspatial_model = none\n"
    )
    config_file.write_text(original_config, encoding="utf-8")
    dialog = SettingsDialog(str(config_file), lambda changed: None)
    spatial_model, _kind = dialog.inputs[("sweep", "spatial_model")]
    spatial_model.setCurrentText("discrete_piston")
    opened = {}

    class TestWindow:
        def __init__(self, path, parent, spatial_model):
            opened.update(path=path, parent=parent, spatial_model=spatial_model)

        def exec(self):
            opened["executed"] = True

    monkeypatch.setattr(directivity_viewer, "DirectivityViewerWindow", TestWindow)

    dialog._open_directivity_viewer()

    assert opened == {
        "path": str(config_file),
        "parent": dialog,
        "spatial_model": "discrete_piston",
        "executed": True,
    }
    assert config_file.read_text(encoding="utf-8") == original_config
    dialog.reject()


@pytest.mark.parametrize("legacy_value", ["true", "1", "yes"])
def test_settings_maps_legacy_true_value_to_spherical_harmonics(tmp_path, legacy_value):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        f"[audio]\nmode = mock_interface\n[sweep]\nspatial_model = {legacy_value}\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda changed: None)

    spatial_model, _kind = dialog.inputs[("sweep", "spatial_model")]
    assert spatial_model.currentText() == "spherical_harmonic"
    dialog.reject()


def test_viewer_maps_legacy_true_model_to_spherical_harmonics(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text("[sweep]\nspatial_model = True\n", encoding="utf-8")
    calls = []

    class TestCanvas(QLabel):
        def __init__(self, model, parent=None):
            super().__init__()
            calls.append("spherical_harmonic")

    monkeypatch.setattr(directivity_viewer, "_DirectivityCanvas", TestCanvas)
    from nfs.utils import spatial
    monkeypatch.setattr(
        spatial,
        "SphericalHarmonicSpeakerModel",
        lambda: SimpleNamespace(model="sh"),
    )

    window = directivity_viewer.DirectivityViewerWindow(str(config_file))

    assert calls == ["spherical_harmonic"]
    window.close()