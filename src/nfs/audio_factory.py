"""Audio factory — config parsing and dependency injection assembly.

Reads the configuration file, constructs all audio pipeline components, resolves
hardware devices, and assembles the correct Audio subclass for the requested mode.
"""

import configparser
from typing import Any

from loguru import logger

from . import _audio_backend as sd
from . import registry
from .audio import Audio, IAudio
from .audio_capture import (BLOCKSIZE, resolve_device_id_by_saved_identity)
from .audio_mock import AudioMock, MockInterfaceAudio
from .audio_signals import (HarmonicInjector, MarkerGenerator, ProtectionFilter,
                            SweepGenerator)
from .sweep_to_ir import AlignmentEngine, DeconvolutionEngine
from .capture_detection import DEFAULT_DETECTORS
from .debug_artifacts import DebugArtifactFactory
from .naming import IRNamingConvention, NamingConventionFactory


class AudioFactory:
    """Parses config and performs Dependency Injection assembling."""

    @staticmethod
    def _normalize_decimal_text(value: str) -> str:
        return value.replace(",", ".")

    @staticmethod
    def _get_required_config(config: configparser.ConfigParser, section: str, key: str, type_func: Any) -> Any:
        if not config.has_option(section, key):
            raise KeyError(f"Missing required config: [{section}] {key}")
        val = config.get(section, key).split('#')[0].split(';')[0].strip()
        if type_func is bool:
            # bool("False") is True in Python: nonempty text is truthy.
            # Decode the configuration value so disabled options stay off.
            value = val.casefold()
            if value not in configparser.ConfigParser.BOOLEAN_STATES:
                raise ValueError(f"Not a boolean: [{section}] {key} = {val}")
            return configparser.ConfigParser.BOOLEAN_STATES[value]
        if type_func in (float, int):
            val = AudioFactory._normalize_decimal_text(val)
        return type_func(val)

    @staticmethod
    def create(config_file: str, audio_section: str = 'audio') -> IAudio:
        """
        Creates Audio instance based on 'mode' config (hardware, loopback, mock).
        """
        config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        config.read(config_file)

        if not config.has_section(audio_section):
            raise KeyError(f"Config file missing [{audio_section}] section")

        # Determine operating mode
        mode = config.get(audio_section, 'mode', fallback='hardware').lower()

        if mode == 'mock':
            return AudioMock()

        # Check registry first for non-standard modes
        # Built-in modes should skip this
        if mode not in ['mock', 'hardware', 'loopback', 'mock_interface']:
            try:
                component = registry.audio.get(mode)
                return component(config_file, audio_section)
            except ValueError:
                pass  # Fallback

        sweep_section = 'sweep'
        if not config.has_section(sweep_section):
            raise KeyError(f"Config file missing [{sweep_section}] section")

        def parse_optional_float(s):
            return None if s.lower() == "none" else float(AudioFactory._normalize_decimal_text(s))

        def optional_config_int(key: str, fallback: int = -1) -> int:
            raw = config.get(audio_section, key, fallback=str(fallback)).split('#')[0].split(';')[0].strip()
            try:
                return int(float(AudioFactory._normalize_decimal_text(raw)))
            except ValueError:
                return fallback

        fs = AudioFactory._get_required_config(config, audio_section, 'fs', int)
        sweep_dur_s = AudioFactory._get_required_config(config, sweep_section, 'sweep_dur_s', float)
        sweep_level_dbfs = AudioFactory._get_required_config(config, sweep_section, 'sweep_level_dbfs', float)

        configured_in = optional_config_int('in_dev')
        configured_out = optional_config_int('out_dev')

        saved_in_name = config.get(audio_section, 'in_dev_name', fallback='').strip()
        saved_in_api = config.get(audio_section, 'in_dev_hostapi', fallback='').strip()
        saved_out_name = config.get(audio_section, 'out_dev_name', fallback='').strip()
        saved_out_api = config.get(audio_section, 'out_dev_hostapi', fallback='').strip()

        hardware_io = None
        if mode == 'mock_interface':
            dev_in = configured_in
            dev_out = configured_out
        else:
            from .audio_process import get_audio_process
            hardware_io = get_audio_process()
            catalog = hardware_io.get_devices_and_channels()
            dev_in = resolve_device_id_by_saved_identity(
                saved_in_name,
                saved_in_api,
                role_label="input",
                require_input=True,
                catalog=catalog,
            )
            dev_out = resolve_device_id_by_saved_identity(
                saved_out_name,
                saved_out_api,
                role_label="output",
                require_output=True,
                catalog=catalog,
            )
        if configured_in >= 0 and dev_in != configured_in:
            logger.info(
                f"Input device '{saved_in_name}' is saved as ID {configured_in}; resolved current ID {dev_in}."
            )
        if configured_out >= 0 and dev_out != configured_out:
            logger.info(
                f"Output device '{saved_out_name}' is saved as ID {configured_out}; resolved current ID {dev_out}."
            )

        hw_config = {
            'dev_in': dev_in,
            'dev_out': dev_out,
            'ch_in_mic': AudioFactory._get_required_config(config, audio_section, 'in_ch_mic', int),
            'ch_in_loop': AudioFactory._get_required_config(config, audio_section, 'in_ch_loop', int),
            'ch_out_spkr': AudioFactory._get_required_config(config, audio_section, 'out_ch_spkr', int),
            'ch_out_ref': AudioFactory._get_required_config(config, audio_section, 'out_ch_ref', int),
            'fs': fs,
            'blocksize': BLOCKSIZE,
            'wasapi_exclusive': AudioFactory._get_required_config(config, audio_section, 'wasapi_exclusive', bool),
        }

        naming_convention_str = config.get(sweep_section, 'naming_convention', fallback='dimitri').strip()
        naming_convention: IRNamingConvention = NamingConventionFactory.create(naming_convention_str)

        debug_saves = True  # Measurement review requires retained raw captures and references.
        debug_handler = DebugArtifactFactory.create(debug_saves, hw_config)

        cap_config = {
            'loopback_correction': config.getboolean(sweep_section, 'loopback_correction', fallback=True),
            'naming_convention': naming_convention_str,
            'debug_saves': debug_saves,
            'sweep_dur_s': sweep_dur_s,
            'sweep_level_dbfs': sweep_level_dbfs,
            'num_sweeps': AudioFactory._get_required_config(config, sweep_section, 'num_sweeps', int),
            'pre_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'pre_sil_ms', float),
            'post_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'post_sil_ms', float),
            'marker_gap_ms': 20.0,  # Fixed marker settling gap; adjust here if needed.
            'max_capture_retries': config.getint(sweep_section, 'max_capture_retries', fallback=3),
            'stop_on_capture_failure': config.getboolean(sweep_section, 'stop_on_capture_failure', fallback=False),
            **{detector.enabled_key: config.getboolean(
                sweep_section, detector.enabled_key, fallback=detector.default_enabled)
                for detector in DEFAULT_DETECTORS},
            'retry_psr_threshold': config.getfloat(sweep_section, 'retry_psr_threshold', fallback=3.0),
            'retry_thd_threshold': config.getfloat(sweep_section, 'retry_thd_threshold', fallback=10.0),
        }

        # Initialize core components
        sweep_gen = SweepGenerator(fs, sweep_dur_s, f1=1.0, level_dbfs=sweep_level_dbfs)
        marker_gen = MarkerGenerator(fs, 100.0, (500.0, 5000.0), sweep_level_dbfs)

        alignment_engine = AlignmentEngine(
            fs,
            cap_config['num_sweeps'],
            AudioFactory._get_required_config(config, sweep_section, 'align_to_first_marker', bool),
            AudioFactory._get_required_config(config, sweep_section, 'mic_tail_taper_ms', float),
            marker_gen.dur_ms
        )

        deconv_engine = DeconvolutionEngine(fs)

        # Gatekeeper logic for optional pipeline stages
        h2_db = AudioFactory._get_required_config(config, sweep_section, 'H2_TEST_DB', parse_optional_float)
        h3_db = AudioFactory._get_required_config(config, sweep_section, 'H3_TEST_DB', parse_optional_float)
        injector = HarmonicInjector(h2_db, h3_db) if (h2_db is not None or h3_db is not None) else None

        protect_hz = AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_HZ', parse_optional_float)
        if protect_hz is not None and protect_hz > 0:
            order = AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_ORDER', int)
            hpf_correction = config.getboolean(sweep_section, 'PROTECT_HPF_CORRECTION', fallback=False)
            hpf_corr_raw = config.get(sweep_section, 'PROTECT_HPF_CORR_DB_CAP', fallback='12.0')
            hpf_corr_raw = hpf_corr_raw.split('#')[0].split(';')[0].strip()
            hpf_corr_db_cap = float(AudioFactory._normalize_decimal_text(hpf_corr_raw))
            filter_engine = ProtectionFilter(fs, protect_hz, order, hpf_correction, hpf_corr_db_cap)
        else:
            filter_engine = None

        kwargs = {
            'hw_config': hw_config,
            'capture_config': cap_config,
            'sweep_gen': sweep_gen,
            'marker_gen': marker_gen,
            'alignment_engine': alignment_engine,
            'deconv_engine': deconv_engine,
            'naming_convention': naming_convention,
            'debug_handler': debug_handler,
            'harmonic_injector': injector,
            'protection_filter': filter_engine
        }

        # Route to the correct class based on mode
        if mode == 'mock_interface':
            # Optionally enable spatial speaker model in mock mode
            spatial_model_str = config.get(sweep_section, 'spatial_model', fallback='false').strip().lower()
            if spatial_model_str in ('true', '1', 'yes', 'spherical_harmonic'):
                from .utils.spatial import SphericalHarmonicSpeakerModel
                kwargs['speaker_model'] = SphericalHarmonicSpeakerModel()
            elif spatial_model_str == 'discrete_piston':
                from .utils.spatial import DiscretePistonSpeakerModel
                kwargs['speaker_model'] = DiscretePistonSpeakerModel()
            return MockInterfaceAudio(**kwargs)

        return Audio(**kwargs, hardware_io=hardware_io)


if __name__ == "__main__":
    # Helper to list devices if run directly
    print("\nAvailable Audio Devices:")
    print(sd.query_devices())
