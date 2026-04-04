import configparser
import datetime
import shutil
import time
from pathlib import Path

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
                 position_log_file: str = 'measurement_positions.csv'):
        self._scanner = scanner
        self._audio = audio
        self._measurement_motion_manager = measurement_motion_manager
        self._position_log_file = position_log_file
        self._clear_position_log()

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
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self) -> None:
        """
        Take a full set of measurements.
        :return: nothing
        """
        # 1. Setup timestamped session directory
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        session_dir = Path(f"./measurements/{timestamp}")
        session_dir.mkdir(parents=True, exist_ok=True)

        # 2. Add a new log sink for this measurement set
        log_file = session_dir / "scanner.log"
        sink_id = logger.add(
            log_file,
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
        )
        logger.info(f"Starting new measurement set in: {session_dir}")

        # 3. Log version info for this session
        log_version_info(log_env=False)

        # 4. Update audio paths and position log path
        if hasattr(self._audio, 'set_session_directory'):
            self._audio.set_session_directory(session_dir)

        original_position_log = self._position_log_file
        session_position_log = str(session_dir / "measurement_points.csv")
        # In nfs.take_measurement_set, keep the original log active, but also log to session dir
        # If we change it, the original is lost for the duration of the set.
        # But we want to use the session one as primary? The test expects the original one to be updated.

        try:
            # We will use self._position_log_file for the current log.
            # To satisfy the requirement of logging to BOTH, we'll keep the test-provided one as primary
            # and session-specific as secondary.
            self._clear_position_log()
            # Also clear the session-specific log if it is different
            if self._position_log_file != session_position_log:
                with open(session_position_log, 'w') as f:
                    f.write('r_xy_mm,phi_deg,z_mm\n')

            self._measurement_motion_manager.move_to_safe_starting_radius()
            total = self._measurement_motion_manager.total_points()
            current = 0
            while not self._measurement_motion_manager.ready():
                self._measurement_motion_manager.next()
                if self._measurement_motion_manager.ready():
                    break

                current += 1
                progress = (current / total) * 100 if total > 0 else 0
                logger.info(f"Measuring point {current} of {total}... {progress:.1f}% complete")

                position = self._scanner.get_position()
                self._append_position_to_file(position)
                # Also append to the session log if it's different
                if self._position_log_file != session_position_log:
                    with open(session_position_log, 'a') as f:
                        f.write(f'{position.r()},{position.t()},{position.z()}\n')

                self._audio.measure_ir(position)

            self._measurement_motion_manager.reset()
            self._measurement_motion_manager.move_to_safe_starting_radius()
            self._scanner.angular_move_to(0.0)

        finally:
            # 5. Cleanup: Restore paths and remove session log sink
            self._position_log_file = original_position_log
            logger.info(f"Measurement set {timestamp} complete.")
            logger.remove(sink_id)

            # 6. Copy the global scanner.log to session dir (if it exists)
            # This fulfills "then copy scanner.log" and "overall log" requirement.
            # But wait, we already have a session log. The user asked for both.
            # "Then we have a log per measurement set and also the overall log."
            # The session sink above ALREADY provides the per-session log.
            # Copying the global log at the end ensures we have the global context too if needed.
            try:
                shutil.copy("scanner.log", session_dir / "overall_scanner.log")
            except Exception as e:
                logger.warning(f"Could not copy global scanner.log: {e}")

    def shutdown(self) -> None:
        """
        Shuts down the scanner system gracefully.

        This method shuts down the scanner system by invoking the shutdown
        functionality of the internal scanner component. It ensures that all
        internal operations are stopped and resources are tidied up properly.

        :return: None
        """
        self._scanner.shutdown()  # turn off stuff and tidy

    def __enter__(self):
        """Context manager enter."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit, ensures shutdown is called."""
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
    def create(scanner: Scanner, config_file: str) -> NearFieldScanner:
        """
        Create a Near Field Scanner based on a config file
        :param scanner:
        :param config_file:
        :return: near field scanner
        """
        setup_logging(config_file)
        
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'nfs'

        plugins_section = config_parser.get(section, 'plugins', fallback='plugins')
        loader.load_plugins(config_file, plugins_section)

        audio_section = config_parser.get(section, 'audio')
        audio = AudioFactory.create(config_file, audio_section)

        motion_manager_section = config_parser.get(section, 'motion_manager')
        measurement_manager = MotionManagerFactory.create(config_file, motion_manager_section, scanner)

        return NearFieldScanner(scanner, audio, measurement_manager)
