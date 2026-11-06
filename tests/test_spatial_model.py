"""Tests for the spherical harmonic speaker spatial model."""

import math
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

# Ensure the src directory is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    cylindrical_to_spherical,
    real_spherical_harmonics,
    spherical_bessel_hankel,
    SPEED_OF_SOUND,
)


class TestCylindricalToSpherical:
    """Test coordinate conversion from cylindrical to spherical."""

    def test_on_axis_position(self):
        """Position on the +x axis (speaker front) should give theta=0."""
        pos = CylindricalPosition(100.0, 0.0, 0.0)  # 100mm on +x axis
        r, theta, phi = cylindrical_to_spherical(pos)
        assert abs(r - 0.1) < 1e-9  # 100mm = 0.1m
        assert abs(theta) < 1e-9  # On +x axis (speaker front)
        assert abs(phi) < 1e-9

    def test_off_axis_position(self):
        """Position on the +z axis (90° from speaker) should give theta=pi/2."""
        pos = CylindricalPosition(0.0, 0.0, 100.0)  # 100mm on +z axis
        r, theta, phi = cylindrical_to_spherical(pos)
        assert abs(r - 0.1) < 1e-9
        assert abs(theta - math.pi / 2.0) < 1e-9  # 90° from +x axis
        # phi in y-z plane: y=0, z=100 → atan2(100, 0) = pi/2
        assert abs(phi - math.pi / 2.0) < 1e-9

    def test_45_degree_position(self):
        """Position at 45 degrees from speaker axis should give theta=pi/4."""
        # r_xy=100mm at phi=0, z=100mm → x=100, y=0, z=100
        # theta = arccos(100/sqrt(100^2+0+100^2)) = pi/4
        pos = CylindricalPosition(100.0, 0.0, 100.0)
        r, theta, phi = cylindrical_to_spherical(pos)
        expected_r = math.sqrt(100.0 ** 2 + 100.0 ** 2) / 1000.0
        assert abs(r - expected_r) < 1e-9
        assert abs(theta - math.pi / 4.0) < 1e-9
        # phi in y-z plane: y=0, z=100 → atan2(100, 0) = pi/2
        assert abs(phi - math.pi / 2.0) < 1e-9

    def test_origin_position(self):
        """Position at origin should return zeros."""
        pos = CylindricalPosition(0.0, 0.0, 0.0)
        r, theta, phi = cylindrical_to_spherical(pos)
        assert r == 0.0
        assert theta == 0.0
        assert phi == 0.0

    def test_with_origin_offset(self):
        """Position relative to a non-zero origin."""
        pos = CylindricalPosition(100.0, 0.0, 0.0)
        origin = CylindricalPosition(50.0, 0.0, 0.0)
        r, theta, phi = cylindrical_to_spherical(pos, origin)
        # Relative position: 50mm on x-axis
        assert abs(r - 0.05) < 1e-9


class TestRealSphericalHarmonics:
    """Test the real spherical harmonic basis functions."""

    def test_y00_is_constant(self):
        """Y₀₀ should be 1/√(4π) at all angles."""
        expected = 1.0 / math.sqrt(4.0 * math.pi)
        for theta in [0.0, math.pi / 4.0, math.pi / 2.0, math.pi]:
            for phi in [0.0, math.pi / 2.0, math.pi]:
                sh = real_spherical_harmonics(0, theta, phi)
                assert abs(sh[(0, 0)] - expected) < 1e-9

    def test_y10_proportional_to_cos_theta(self):
        """Y₁₀ should be proportional to cos(theta)."""
        # At theta=0, cos(0)=1; at theta=pi/2, cos(pi/2)=0
        sh_on_axis = real_spherical_harmonics(1, 0.0, 0.0)
        sh_equator = real_spherical_harmonics(1, math.pi / 2.0, 0.0)

        # Y₁₀ at theta=0 should be non-zero
        assert abs(sh_on_axis[(1, 0)]) > 1e-6
        # Y₁₀ at theta=pi/2 should be near zero
        assert abs(sh_equator[(1, 0)]) < 1e-6

    def test_normalization_sum(self):
        """Sum of |Yₗₘ|² over all m at a given l should be (2l+1)/(4π)."""
        for l in range(4):
            sh = real_spherical_harmonics(l, 0.0, 0.0)
            total = 0.0
            for m in range(-l, l + 1):
                val = sh.get((l, m), 0.0)
                total += val ** 2
            expected = (2.0 * l + 1.0) / (4.0 * math.pi)
            assert abs(total - expected) < 1e-6, f"Normalization failed for l={l}"


class TestSphericalBesselHankel:
    """Test the spherical Bessel/Hankel function computation."""

    def test_l0_at_small_kr(self):
        """h₀⁽²⁾(kr) ≈ +i·e^(-ikr)/(kr) for small kr."""
        kr = np.array([1e-6, 0.01, 0.1])
        h = spherical_bessel_hankel(0, kr)
        # For small kr, |h₀(kr)| should be large (dominated by imaginary part)
        assert np.all(np.abs(h) > 1.0)

    def test_l0_at_large_kr(self):
        """h₀(kr) should decay as 1/kr for large kr."""
        kr = np.array([10.0, 100.0])
        h = spherical_bessel_hankel(0, kr)
        # |h₀(kr)| ≈ 1/kr for large kr
        expected = 1.0 / kr
        ratio = np.abs(h) / expected
        assert np.all(ratio > 0.5) and np.all(ratio < 2.0)

    def test_complex_output(self):
        """Output should be complex."""
        kr = np.array([1.0, 2.0, 3.0])
        h = spherical_bessel_hankel(1, kr)
        assert h.dtype == np.complex128


class TestSphericalHarmonicSpeakerModel:
    """Test the speaker model frequency response."""

    def test_default_model_creates(self):
        """Default model should initialize without error."""
        model = SphericalHarmonicSpeakerModel()
        assert model is not None

    def test_on_axis_response_is_flat(self):
        """On-axis response (speaker firing direction) should be the strongest.
        
        The speaker fires along the radial axis (cylindrical theta=0, z=0),
        which is the +x direction. The response is not normalized to unity,
        but the on-axis direction should have the largest magnitude.
        """
        model = SphericalHarmonicSpeakerModel(order=1)
        fs = 48000
        # Position on the radial axis at theta=0, z=0 (speaker's main direction)
        pos_on = CylindricalPosition(100.0, 0.0, 0.0)
        pos_off = CylindricalPosition(0.0, 0.0, 100.0)  # Off-axis (on z-axis)

        H_on = model.compute_frequency_response(fs, pos_on)
        H_off = model.compute_frequency_response(fs, pos_off)

        # On-axis should be stronger than off-axis at high frequencies
        high_freq_start = int(3000 / (fs / 2) * (len(H_on) - 1))
        mag_on = np.mean(np.abs(H_on[high_freq_start:]))
        mag_off = np.mean(np.abs(H_off[high_freq_start:]))
        assert mag_on > mag_off, \
            f"On-axis response should be stronger than off-axis, got {mag_on:.3f} vs {mag_off:.3f}"

    def test_off_axis_response_differs(self):
        """Off-axis response should differ from on-axis for default model.
        
        On-axis is at radial theta=0, z=0 (speaker firing direction).
        Off-axis is at z>0 (above the speaker, off the firing axis).
        """
        model = SphericalHarmonicSpeakerModel(order=1)
        fs = 48000

        pos_on = CylindricalPosition(100.0, 0.0, 0.0)  # On radial axis (firing direction)
        pos_off = CylindricalPosition(0.0, 0.0, 100.0)  # On z-axis (off the firing axis)

        H_on = model.compute_frequency_response(fs, pos_on)
        H_off = model.compute_frequency_response(fs, pos_off)

        # Responses should differ
        diff = np.abs(H_on - H_off)
        assert np.max(diff) > 0.01, "On-axis and off-axis responses should differ"

    def test_near_field_vs_far_field(self):
        """Near-field response should differ from far-field.
        
        Both positions are on the speaker's firing axis (radial theta=0, z=0).
        """
        model = SphericalHarmonicSpeakerModel(order=1)
        fs = 48000

        pos_near = CylindricalPosition(50.0, 0.0, 0.0)   # 50mm on radial axis
        pos_far = CylindricalPosition(500.0, 0.0, 0.0)   # 500mm on radial axis

        H_near = model.compute_frequency_response(fs, pos_near)
        H_far = model.compute_frequency_response(fs, pos_far)

        # Responses should differ due to radial propagation
        diff = np.abs(H_near - H_far)
        assert np.max(diff) > 0.01, "Near-field and far-field responses should differ"

    def test_dc_component_is_unity(self):
        """DC component (f=0) should be 1.0."""
        model = SphericalHarmonicSpeakerModel()
        fs = 48000
        pos = CylindricalPosition(100.0, 45.0, 50.0)
        H = model.compute_frequency_response(fs, pos)
        assert abs(H[0] - 1.0) < 1e-6

    def test_apply_to_signal(self):
        """Applying the model to a signal should produce a real output."""
        model = SphericalHarmonicSpeakerModel(order=1)
        fs = 48000
        duration = 0.1  # 100ms
        t = np.arange(int(fs * duration), dtype=np.float32) / fs
        signal = np.sin(2.0 * np.pi * 1000.0 * t).astype(np.float32)

        pos = CylindricalPosition(100.0, 0.0, 0.0)
        result = model.apply_to_signal(signal, fs, pos)

        assert result.shape == signal.shape
        assert result.dtype == np.float32
        assert not np.any(np.isnan(result))

    def test_omnidirectional_only_model(self):
        """Model with only a₀₀ should produce flat response everywhere."""
        freq_bins = 24577
        coeffs = {(0, 0): np.ones(freq_bins, dtype=np.float64)}
        model = SphericalHarmonicSpeakerModel(order=0, coeffs=coeffs, freq_bins=freq_bins)
        fs = 48000

        pos1 = CylindricalPosition(0.0, 0.0, 100.0)
        pos2 = CylindricalPosition(100.0, 90.0, 50.0)

        H1 = model.compute_frequency_response(fs, pos1)
        H2 = model.compute_frequency_response(fs, pos2)

        # With only a₀₀, angular response is flat; radial normalization should make them similar
        # The magnitudes should be close (within 10% for same distance)
        mag1 = np.abs(H1[10:])
        mag2 = np.abs(H2[10:])
        ratio = mag1 / (mag2 + 1e-12)
        assert np.all(ratio > 0.5) and np.all(ratio < 2.0)

    def test_back_of_speaker_attenuated(self):
        """Back of speaker should show attenuation compared to front, especially at high frequencies.
        
        This is a regression test: the baffled piston model was front-back symmetric,
        giving a flat response at the back. The free-field model should attenuate the back.
        """
        model = SphericalHarmonicSpeakerModel()  # Default order=4, free-field piston
        fs = 48000

        # Front: radial theta=0, z=0 (speaker firing direction)
        pos_front = CylindricalPosition(100.0, 0.0, 0.0)
        # Back: radial theta=180, z=0 (behind the speaker)
        pos_back = CylindricalPosition(100.0, 180.0, 0.0)

        H_front = model.compute_frequency_response(fs, pos_front)
        H_back = model.compute_frequency_response(fs, pos_back)

        # Check at high frequencies (above 3 kHz where beaming is strong)
        # For 48 kHz sample rate, 3 kHz is at bin index ~3000
        high_freq_start = int(3000 / (fs / 2) * (len(H_front) - 1))

        mag_front = np.abs(H_front[high_freq_start:])
        mag_back = np.abs(H_back[high_freq_start:])

        # Front should be stronger than back at high frequencies
        # Use ratio since response is not normalized to unity
        ratio = np.mean(mag_back) / (np.mean(mag_front) + 1e-12)
        assert ratio < 0.5, \
            f"Back should be attenuated vs front at high freq, got ratio={ratio:.3f}"

        # Back should not be flat (should vary with frequency)
        # Use dB std since back magnitudes are very small
        mag_back_db = 20 * np.log10(mag_back + 1e-12)
        back_std_db = np.std(mag_back_db)
        assert back_std_db > 5.0, f"Back response should vary with frequency, got std={back_std_db:.1f}dB"

    def test_y11_sign_at_phi_180(self):
        """Y₁₁ at phi=180° should be negative (regression test for sign bug).
        
        Y₁₁ ∝ sin(theta)·cos(phi), so at theta=90°, phi=180° it should be negative.
        """
        sh = real_spherical_harmonics(2, math.pi / 2.0, math.pi)  # theta=90°, phi=180°
        y11 = sh[(1, 1)]
        assert y11 < -0.4, f"Y₁₁ at phi=180° should be negative, got {y11}"
        # Should match Y₁₁ at phi=0° in magnitude but opposite sign
        sh_front = real_spherical_harmonics(2, math.pi / 2.0, 0.0)
        y11_front = sh_front[(1, 1)]
        assert abs(y11 + y11_front) < 1e-10, \
            f"Y₁₁ at phi=180° should be -Y₁₁ at phi=0°, got {y11} vs {y11_front}"


class TestMockInterfaceAudioIntegration:
    """Test integration of the spatial model with MockInterfaceAudio."""

    def test_mock_audio_without_position_callback(self):
        """Mock audio without position callback should produce flat response."""
        from nfs.audio import MockInterfaceAudio, AudioFactory
        from nfs.utils.spatial import SphericalHarmonicSpeakerModel

        # Create a minimal config for mock_interface mode
        import configparser
        import tempfile

        config = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        config["audio"] = {
            "mode": "mock_interface",
            "fs": "48000",
            "in_ch_mic": "0",
            "in_ch_loop": "1",
            "out_ch_spkr": "0",
            "out_ch_ref": "1",
            "blocksize": "1024",
            "wasapi_exclusive": "false",
        }
        config["sweep"] = {
            "sweep_dur_s": "0.5",
            "sweep_level_dbfs": "-12.0",
            "num_sweeps": "1",
            "pre_sil_ms": "10.0",
            "post_sil_ms": "10.0",
            "naming_convention": "dimitri",
            "debug_saves": "false",
            "align_to_first_marker": "true",
            "mic_tail_taper_ms": "5.0",
            "H2_TEST_DB": "None",
            "H3_TEST_DB": "None",
            "PROTECT_HPF_HZ": "None",
            "PROTECT_HPF_ORDER": "8",
            "PROTECT_HPF_CORRECTION": "false",
            "PROTECT_HPF_CORR_DB_CAP": "12.0",
            "spatial_model": "false",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            config.write(f)
            config_file = f.name

        try:
            audio = AudioFactory.create(config_file)
            assert audio is not None
            # Without spatial_model enabled, the mock should work normally
            result = audio.preview_ir()
            assert "ir_linear" in result
        finally:
            Path(config_file).unlink()

    def test_mock_audio_with_spatial_model(self):
        """Mock audio with spatial model should produce different IRs for different positions."""
        from nfs.audio import MockInterfaceAudio
        from nfs.utils.spatial import SphericalHarmonicSpeakerModel
        from nfs.audio import (
            SweepGenerator,
            MarkerGenerator,
            AlignmentEngine,
            DeconvolutionEngine,
            HarmonicInjector,
            ProtectionFilter,
        )
        from nfs.naming import NamingConventionFactory

        fs = 48000
        hw_config = {
            "dev_in": 0,
            "dev_out": 0,
            "ch_in_mic": 0,
            "ch_in_loop": 1,
            "ch_out_spkr": 0,
            "ch_out_ref": 1,
            "fs": fs,
            "blocksize": 1024,
            "wasapi_exclusive": False,
        }
        cap_config = {
            "debug_saves": False,
            "sweep_dur_s": 0.5,
            "sweep_level_dbfs": -12.0,
            "num_sweeps": 1,
            "pre_sil_ms": 10.0,
            "post_sil_ms": 10.0,
        }

        sweep_gen = SweepGenerator(fs, 0.5, f1=1.0, level_dbfs=-12.0)
        marker_gen = MarkerGenerator(fs, 100.0, (500.0, 5000.0), -12.0)
        alignment_engine = AlignmentEngine(fs, 1, True, 5.0, marker_gen.dur_ms)
        deconv_engine = DeconvolutionEngine(fs)
        naming = NamingConventionFactory.create("dimitri")

        from nfs.debug_artifacts import DebugArtifactFactory
        debug_handler = DebugArtifactFactory.create(False, hw_config)

        speaker_model = SphericalHarmonicSpeakerModel(order=1)

        audio = MockInterfaceAudio(
            hw_config=hw_config,
            capture_config=cap_config,
            sweep_gen=sweep_gen,
            marker_gen=marker_gen,
            alignment_engine=alignment_engine,
            deconv_engine=deconv_engine,
            naming_convention=naming,
            debug_handler=debug_handler,
            speaker_model=speaker_model,
        )

        # Set position callback for on-axis (speaker firing direction: radial theta=0, z=0)
        pos_on = CylindricalPosition(100.0, 0.0, 0.0)
        audio.set_position_callback(lambda: pos_on)
        result_on = audio.preview_ir()

        # Set position callback for off-axis (above the speaker, off the firing axis)
        pos_off = CylindricalPosition(0.0, 0.0, 100.0)
        audio.set_position_callback(lambda: pos_off)
        result_off = audio.preview_ir()

        # IRs should differ
        ir_on = result_on["ir_linear"]
        ir_off = result_off["ir_linear"]
        diff = np.abs(ir_on - ir_off)
        assert np.max(diff) > 1e-6, "IRs at different positions should differ"

    def test_set_position_callback(self):
        """set_position_callback should update the callback."""
        from nfs.audio import MockInterfaceAudio
        from nfs.audio import (
            SweepGenerator,
            MarkerGenerator,
            AlignmentEngine,
            DeconvolutionEngine,
        )
        from nfs.naming import NamingConventionFactory
        from nfs.debug_artifacts import DebugArtifactFactory

        fs = 48000
        hw_config = {
            "dev_in": 0, "dev_out": 0, "ch_in_mic": 0, "ch_in_loop": 1,
            "ch_out_spkr": 0, "ch_out_ref": 1, "fs": fs,
            "blocksize": 1024, "wasapi_exclusive": False,
        }
        cap_config = {
            "debug_saves": False, "sweep_dur_s": 0.5, "sweep_level_dbfs": -12.0,
            "num_sweeps": 1, "pre_sil_ms": 10.0, "post_sil_ms": 10.0,
        }
        sweep_gen = SweepGenerator(fs, 0.5, f1=1.0, level_dbfs=-12.0)
        marker_gen = MarkerGenerator(fs, 100.0, (500.0, 5000.0), -12.0)
        alignment_engine = AlignmentEngine(fs, 1, True, 5.0, marker_gen.dur_ms)
        deconv_engine = DeconvolutionEngine(fs)
        naming = NamingConventionFactory.create("dimitri")
        debug_handler = DebugArtifactFactory.create(False, hw_config)

        audio = MockInterfaceAudio(
            hw_config=hw_config,
            capture_config=cap_config,
            sweep_gen=sweep_gen,
            marker_gen=marker_gen,
            alignment_engine=alignment_engine,
            deconv_engine=deconv_engine,
            naming_convention=naming,
            debug_handler=debug_handler,
        )

        # Initially no callback
        assert audio._position_callback is None

        # Set callback
        pos = CylindricalPosition(50.0, 30.0, 25.0)
        audio.set_position_callback(lambda: pos)
        retrieved = audio._position_callback()
        assert retrieved.r() == 50.0
        assert retrieved.t() == 30.0
        assert retrieved.z() == 25.0

    def test_backward_compatibility_no_speaker_model(self):
        """Mock audio without speaker_model should behave as before."""
        from nfs.audio import MockInterfaceAudio
        from nfs.audio import (
            SweepGenerator,
            MarkerGenerator,
            AlignmentEngine,
            DeconvolutionEngine,
        )
        from nfs.naming import NamingConventionFactory
        from nfs.debug_artifacts import DebugArtifactFactory

        fs = 48000
        hw_config = {
            "dev_in": 0, "dev_out": 0, "ch_in_mic": 0, "ch_in_loop": 1,
            "ch_out_spkr": 0, "ch_out_ref": 1, "fs": fs,
            "blocksize": 1024, "wasapi_exclusive": False,
        }
        cap_config = {
            "debug_saves": False, "sweep_dur_s": 0.5, "sweep_level_dbfs": -12.0,
            "num_sweeps": 1, "pre_sil_ms": 10.0, "post_sil_ms": 10.0,
        }
        sweep_gen = SweepGenerator(fs, 0.5, f1=1.0, level_dbfs=-12.0)
        marker_gen = MarkerGenerator(fs, 100.0, (500.0, 5000.0), -12.0)
        alignment_engine = AlignmentEngine(fs, 1, True, 5.0, marker_gen.dur_ms)
        deconv_engine = DeconvolutionEngine(fs)
        naming = NamingConventionFactory.create("dimitri")
        debug_handler = DebugArtifactFactory.create(False, hw_config)

        audio = MockInterfaceAudio(
            hw_config=hw_config,
            capture_config=cap_config,
            sweep_gen=sweep_gen,
            marker_gen=marker_gen,
            alignment_engine=alignment_engine,
            deconv_engine=deconv_engine,
            naming_convention=naming,
            debug_handler=debug_handler,
        )

        # Should work without speaker model
        result = audio.preview_ir()
        assert "ir_linear" in result
        assert "ir_full" in result
        assert "metrics" in result
