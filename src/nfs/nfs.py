import configparser
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .logging_config import setup_logging, log_version_info
from loguru import logger

from . import loader
from .audio import AudioFactory, IAudio
from .motion_manager import MotionManagerFactory
from .scanner import Scanner


class NearFieldScanner:
    """
    The NearFieldScanner class is responsible for taking single and multiple
    acoustic measurements using a scanner and an audio interface. It interacts
    with a motion manager to handle positioning for a series of measurements
    and ensures safe and proper operation of the hardware.

    :ivar _scanner: The scanner object used to manage the scanning hardware.
    :type _scanner: Scanner
    :ivar _audio: The audio interface for measurement and signal capture.
    :type _audio: IAudio
    :ivar _measurement_motion_manager: Manager responsible for controlling motions
        during measurements and handling safe transitions.
    :type _measurement_motion_manager: Any
    :ivar _position_log_file: Path to the file where measurement positions are logged.
    :type _position_log_file: str
    """
    def __init__(self,
                 scanner: Scanner,
                 audio: IAudio,
                 measurement_motion_manager,
                 position_log_file: str = 'measurement_positions.csv',
                 config_file: str = None):
        """
        Initialize the NearFieldScanner.

        :param scanner: The scanner object used to manage the scanning hardware.
        :param audio: The audio interface for measurement and signal capture.
        :param measurement_motion_manager: Manager responsible for controlling motions during measurements.
        :param position_log_file: Path to the file where measurement positions are logged.
        :param config_file: Path to the configuration file used.
        """
        self._scanner = scanner
        self._audio = audio
        self._measurement_motion_manager = measurement_motion_manager
        self._position_log_file = position_log_file
        self._config_file = config_file
        self._project_dir = Path.cwd()
        self._measurement_pause_requested = threading.Event()
        self._measurement_stop_requested = threading.Event()
        self._measurement_running = False
        self._measurement_progress_lock = threading.Lock()
        self._measurement_progress = {
            "status": "ready",
            "current": 0,
            "total": 0,
            "timestamp": time.monotonic(),
            "eta_seconds": None,
        }
        self._clear_position_log()

    def _single_measurements_dir(self) -> Path:
        return self._project_dir / "single_measurements"

    def _measurement_set_dir(self) -> Path:
        return self._project_dir / "measurement_set"

    @staticmethod
    def _safe_measurement_set_name(name: str | None) -> str:
        if name:
            cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(name).strip())
            cleaned = "_".join(part for part in cleaned.split("_") if part)
            if cleaned:
                return cleaned
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _clear_position_log(self) -> None:
        """
        Clears the position log file at initialization and writes CSV header.
        :return: None
        """
        with open(self._position_log_file, 'w') as f:
            f.write('r_xy_mm,phi_deg,z_mm\n')
        logger.info(f'Position log file cleared: {self._position_log_file}')

    def _append_position_to_file(self, position) -> None:
        """
        Appends the measurement position to the log file as CSV with numeric values only.
        :param position: The position to be logged (CylindricalPosition)
        :return: None
        """
        with open(self._position_log_file, 'a') as f:
            f.write(f'{position.r()},{position.t()},{position.z()}\n')
        logger.debug(f'Position logged: {position}')

    def take_single_measurement(self) -> None:
        """
        This function takes a single measurement. This is handy for checking
        the audio levels.
        :return: Nothing
        """
        if hasattr(self._audio, 'set_session_directory'):
            self._audio.set_session_directory(self._single_measurements_dir())
        self._audio.measure_ir(self._scanner.get_position())

    def test_sweep(self):
        """
        Run the current sweep settings at the current position without saving files.

        This is intended for level/protection setup. The returned IR can be shown
        in the live-capture plots without creating permanent measurement output.
        """
        return self._audio.measure_ir(self._scanner.get_position(), "TEST", save=False)

    def pause_measurement_set(self) -> None:
        """Request a clean pause before the next measurement point."""
        if self._measurement_running:
            logger.info("Measurement set pause requested.")
            self._measurement_pause_requested.set()

    def resume_measurement_set(self) -> None:
        """Resume a paused measurement set."""
        logger.info("Measurement set resume requested.")
        self._measurement_pause_requested.clear()

    def stop_measurement_set(self) -> None:
        """Request a clean stop before any further measurement points are taken."""
        if self._measurement_running:
            logger.info("Measurement set stop requested.")
            self._measurement_stop_requested.set()
            self._measurement_pause_requested.clear()

    def is_measurement_set_running(self) -> bool:
        return self._measurement_running

    def is_measurement_set_paused(self) -> bool:
        return self._measurement_pause_requested.is_set()

    def get_measurement_progress(self) -> dict[str, Any]:
        with self._measurement_progress_lock:
            return dict(self._measurement_progress)

    def _wait_while_measurement_paused(self) -> bool:
        if self._measurement_pause_requested.is_set():
            logger.info("Measurement set paused.")
        while self._measurement_pause_requested.is_set():
            if self._measurement_stop_requested.is_set():
                return False
            time.sleep(0.1)
        return not self._measurement_stop_requested.is_set()

    def take_measurement_set(
        self,
        measurement_set_name: str | None = None,
        overwrite: bool = False,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        """
        Take a full set of measurements.
        :return: nothing
        """
        self._measurement_pause_requested.clear()
        self._measurement_stop_requested.clear()
        self._measurement_running = True
        current = 0
        total = 0
        started_at = None
        eta_seconds = None
        stopped_early = False

        # 1. Setup this measurement set's output directory
        session_name = self._safe_measurement_set_name(measurement_set_name)
        measurement_dir = self._measurement_set_dir()
        if measurement_dir.exists() and overwrite:
            shutil.rmtree(measurement_dir)
        measurement_dir.mkdir(parents=True, exist_ok=True)

        # 2. Add a new log sink for this measurement set
        log_dir = self._project_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "Scanner.log"
        sink_id = logger.add(
            log_file,
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
        )
        logger.info(f"Starting measurement set {session_name} in: {self._project_dir}")

        # 3. Log version info for this session
        log_version_info(log_env=False)

        # 4. Update audio paths and position log path
        if hasattr(self._audio, 'set_session_directory'):
            self._audio.set_session_directory(measurement_dir)

        try:
            self._clear_position_log()

            self._measurement_motion_manager.move_to_safe_starting_radius()
            total = self._measurement_motion_manager.total_points()
            started_at = time.monotonic()
            self._emit_measurement_progress(
                progress_callback,
                "started",
                current,
                total,
                timestamp=started_at,
                eta_seconds=eta_seconds,
            )
            while not self._measurement_motion_manager.ready():
                if not self._wait_while_measurement_paused():
                    logger.info("Measurement set stopped before next point.")
                    stopped_early = True
                    break

                self._measurement_motion_manager.next()
                if self._measurement_motion_manager.ready():
                    break

                if self._measurement_stop_requested.is_set():
                    logger.info("Measurement set stopped after current move.")
                    stopped_early = True
                    break

                current += 1
                progress = (current / total) * 100 if total > 0 else 0
                logger.info(f"Measuring point {current} of {total}... {progress:.1f}% complete")

                position = self._scanner.get_position()
                self._append_position_to_file(position)

                self._audio.measure_ir(position)
                timestamp = time.monotonic()
                if started_at is not None and total > 0 and current >= 2:
                    seconds_per_point = (timestamp - started_at) / current
                    eta_seconds = seconds_per_point * max(0, total - current)
                self._emit_measurement_progress(
                    progress_callback,
                    "point_complete",
                    current,
                    total,
                    timestamp=timestamp,
                    eta_seconds=eta_seconds,
                )

            self._measurement_motion_manager.reset()
            self._measurement_motion_manager.move_to_safe_starting_radius()
            self._scanner.angular_move_to(0.0)

        finally:
            final_current = current
            if not stopped_early and total > 0:
                final_current = total
            finished_eta = 0 if total > 0 and final_current >= total else eta_seconds
            self._emit_measurement_progress(
                progress_callback,
                "finished",
                final_current,
                total,
                eta_seconds=finished_eta,
            )
            self._measurement_running = False
            self._measurement_pause_requested.clear()
            self._measurement_stop_requested.clear()
            # 5. Cleanup: Restore paths and remove session log sink
            logger.info(f"Measurement set {session_name} complete.")
            logger.remove(sink_id)

    def _emit_measurement_progress(
        self,
        progress_callback: Optional[Callable[[dict[str, Any]], None]],
        status: str,
        current: int,
        total: int,
        timestamp: float | None = None,
        eta_seconds: float | None = None,
    ) -> None:
        event = {
            "status": status,
            "current": current,
            "total": total,
            "timestamp": timestamp if timestamp is not None else time.monotonic(),
            "eta_seconds": eta_seconds,
        }
        with self._measurement_progress_lock:
            self._measurement_progress = dict(event)
        if progress_callback is None:
            return
        try:
            progress_callback(dict(event))
        except Exception as exc:
            logger.warning(f"Measurement progress callback failed: {exc}")

    def set_project_directory(self, project_dir: str | Path) -> None:
        self._project_dir = Path(project_dir)
        self._project_dir.mkdir(parents=True, exist_ok=True)
        self._position_log_file = str(self._project_dir / "measurement_positions.csv")
        if hasattr(self._audio, 'set_session_directory'):
            self._audio.set_session_directory(self._single_measurements_dir())
        self._clear_position_log()

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.
        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        :return: None
        """
        self._audio.play_sine(frequency, level_dbfs, duration_s)

    def stop_sine(self) -> None:
        """
        Stops any active sine wave playback.
        :return: None
        """
        self._audio.stop_sine()

    def shutdown(self) -> None:
        """
        Shuts down the scanner system gracefully.

        This method shuts down the scanner system by invoking the shutdown
        functionality of the internal scanner component and stopping any
        active audio signals. It ensures that all internal operations are 
        stopped and resources are tidied up properly.

        :return: None
        """
        try:
            self.stop_sine()
        except Exception as e:
            logger.warning(f"Error stopping sine during shutdown: {e}")
        self._scanner.shutdown()  # turn off stuff and tidy

    def __enter__(self):
        """
        Context manager enter.

        :return: The NearFieldScanner instance.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Context manager exit, ensures shutdown is called.

        :param exc_type: Exception type if an exception occurred.
        :param exc_val: Exception value if an exception occurred.
        :param exc_tb: Traceback if an exception occurred.
        """
        self.shutdown()


class NearFieldScannerFactory:
    """
    A factory class for creating Near Field Scanner objects.

    This class provides a method to create a Near Field Scanner by using
    the given scanner and a configuration file. It handles loading plugins,
    creating necessary audio configurations, parsing the configuration file,
    and initializing the measurement manager for the scanner.
    """
    @staticmethod
    def create(scanner: Scanner, config_file: str = "config.ini") -> NearFieldScanner:
        """
        Create a Near Field Scanner based on a config file.

        :param scanner: The scanner instance to use.
        :param config_file: Path to the configuration file. Default is "config.ini".
        :return: A fully initialized NearFieldScanner instance.
        """
        setup_logging(config_file)
        
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'nfs'

        plugins_section = config_parser.get(section, 'plugins', fallback='plugins')
        # We need to ensure plugins are reloaded/re-registered if they changed,
        # but loader.load_plugins currently doesn't support easy unregistering.
        # For now, we just call it again which might be redundant but safe for most plugins.
        loader.load_plugins(config_file, plugins_section)

        audio_section = config_parser.get(section, 'audio')
        audio = AudioFactory.create(config_file, audio_section)

        motion_manager_section = config_parser.get(section, 'motion_manager')
        measurement_manager = MotionManagerFactory.create(config_file, motion_manager_section, scanner)

        return NearFieldScanner(scanner, audio, measurement_manager, config_file=config_file)
