import configparser
import math
import re
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from grbl_streamer import GrblStreamer  # type: ignore
from loguru import logger

from nfs.datatypes import CylindricalPosition, GrblMachineState


def _debug_serial_comms_enabled(config_parser: configparser.ConfigParser) -> bool:
    return config_parser.getboolean("debug", "serial_comms", fallback=False)


class IGrblController(ABC):
    """
    Represents an interface for a GRBL controller.

    This abstract base class defines a set of methods to communicate
    and control a GRBL-powered device. It provides functionality for
    sending messages, shutting down the controller, and querying the
    device's position. Implementations of this interface must provide
    concrete behavior for these methods to suit specific GRBL device
    requirements.
    """
    @abstractmethod
    def shutdown(self) -> None:
        """
        Gracefully shut down the GRBL controller.
        """
        pass

    @abstractmethod
    def send(self, message: str) -> None:
        """
        Send a G-code command to the GRBL controller.

        :param message: The G-code string to send.
        """
        pass

    @abstractmethod
    def send_and_wait_for_move_ready(self, message: str) -> None:
        """
        Send a movement command and wait until the controller is ready for the next one.

        :param message: The G-code movement command.
        """
        pass

    @abstractmethod
    def killalarm(self) -> None:
        """
        Clear the GRBL alarm state ($X).
        """
        pass

    @abstractmethod
    def softreset(self) -> None:
        """
        Perform a soft reset of the GRBL controller.
        """
        pass

    @abstractmethod
    def hold(self) -> None:
        """
        Initiate a feed hold (!) to pause movement.
        """
        pass

    @abstractmethod
    def get_position(self) -> CylindricalPosition:
        """
        Return the current cylindrical position of the scanner.

        :return: A CylindricalPosition object.
        """
        pass

    def get_machine_position(self) -> Optional[CylindricalPosition]:
        """
        Return the current machine-coordinate cylindrical position, if available.

        :return: A CylindricalPosition object or None.
        """
        return self.get_position()

    @abstractmethod
    def get_state(self) -> GrblMachineState:
        """
        Return the current normalized state of the GRBL controller.

        :return: A GrblMachineState enum value.
        """
        pass

    @abstractmethod
    def get_state_raw(self) -> str:
        """
        Return the raw state string from the GRBL controller.

        :return: The raw state string.
        """
        pass

    @abstractmethod
    def set_on_state_update_callback(self, callback) -> None:
        """
        Register a callback for state and position updates.

        :param callback: A callable that receives (position, state, machine_position).
        """
        pass

    def force_position_update(self):
        """
        Force a position update from the controller.
        """
        pass


class GrblControllerMock(IGrblController):
    """
    Mock implementation of the GrblController interface.

    This class is used to simulate the behavior of a GRBL controller without
    interacting with actual hardware. It provides basic mock functionality for
    testing purposes, such as simulating message sending, obtaining position,
    and shutting down operations. This allows developers to test systems that
    rely on GRBL controllers in a controlled and predictable manner.
    """
    def shutdown(self) -> None:
        logger.trace(f'MockingShutting down')

    def send(self, message: str) -> None:
        logger.trace(f'Mocking sending message: {message}')
        # Handle coordinate extraction if needed, but for now we just track Y/Z/X
        # In nfs, R=Y, T=Z, Z=X
        import re
        x_match = re.search(r'X([-+]?\d*\.?\d+)', message)
        y_match = re.search(r'Y([-+]?\d*\.?\d+)', message)
        z_match = re.search(r'Z([-+]?\d*\.?\d+)', message)

        if x_match:
            self._pos_z = float(x_match.group(1))

        if y_match:
            self._pos_r = float(y_match.group(1))

        if z_match:
            self._pos_t = float(z_match.group(1))

    def send_and_wait_for_move_ready(self, message: str) -> None:
        logger.trace(f'Mocking send and wait: {message}')
        self.send(message)

    def killalarm(self) -> None:
        logger.trace(f'Mocking killalarm')

    def softreset(self) -> None:
        logger.trace(f'Mocking softreset')

    def hold(self) -> None:
        logger.trace(f'Mocking hold')

    def __init__(self):
        self._pos_r = 0.0
        self._pos_t = 0.0
        self._pos_z = 0.0

    def get_position(self) -> CylindricalPosition:
        return CylindricalPosition(self._pos_r, self._pos_t, self._pos_z)

    def get_machine_position(self) -> CylindricalPosition:
        return self.get_position()

    def get_state(self) -> GrblMachineState:
        return GrblMachineState.IDLE

    def get_state_raw(self) -> str:
        return "Idle"

    def set_on_state_update_callback(self, callback) -> None:
        pass

    def force_position_update(self):
        logger.trace(f'Mocking force position update')
        pass


def _target_from_message(
    message: str,
    current_r: float,
    current_t: float,
    current_z: float,
) -> Optional[tuple[float, float, float]]:
    x_match = re.search(r'X([-+]?\d*\.?\d+)', message, re.IGNORECASE)
    y_match = re.search(r'Y([-+]?\d*\.?\d+)', message, re.IGNORECASE)
    z_match = re.search(r'Z([-+]?\d*\.?\d+)', message, re.IGNORECASE)
    if not any((x_match, y_match, z_match)):
        return None
    target_r = current_r
    target_t = current_t
    target_z = current_z
    if x_match:
        target_z = float(x_match.group(1))
    if y_match:
        target_r = float(y_match.group(1))
    if z_match:
        target_t = float(z_match.group(1))
    return target_r, target_t, target_z


class GrblControllerMockSimulatedDRO(IGrblController):
    """
    Mock GRBL controller with real-time movement and DRO/status callbacks.

    This is intended for reproducing UI load from real GRBLHAL status reports
    while running without hardware. The plain Mock controller remains instant
    and quiet.
    """
    def __init__(
        self,
        *,
        linear_speed_mm_s: float = 500.0,
        angular_speed_deg_s: float = 180.0,
        status_hz: float = 5.0,
    ):
        self._pos_r = 0.0
        self._pos_t = 0.0
        self._pos_z = 0.0
        self._machine_pos_r = 0.0
        self._machine_pos_t = 0.0
        self._machine_pos_z = 0.0
        self._state = GrblMachineState.IDLE
        self._state_raw = "Idle"
        self._on_state_update_callback = None
        self._linear_speed_mm_s = max(1.0, float(linear_speed_mm_s))
        self._angular_speed_deg_s = max(1.0, float(angular_speed_deg_s))
        self._status_interval_s = 1.0 / max(1.0, float(status_hz))
        self._lock = threading.Lock()
        self._motion_done = threading.Event()
        self._motion_done.set()
        self._shutdown = False

    def shutdown(self) -> None:
        logger.trace(f'MockingShutting down')
        self._shutdown = True
        self._motion_done.set()

    def send(self, message: str) -> None:
        logger.trace(f'Mocking sending message: {message}')
        if self._is_motion_command(message):
            self._start_motion(message)
            return
        target = self._target_from_message(message)
        if target is not None:
            with self._lock:
                self._pos_r, self._pos_t, self._pos_z = target
                self._machine_pos_r, self._machine_pos_t, self._machine_pos_z = target
            self._emit_state_update()

    def send_and_wait_for_move_ready(self, message: str) -> None:
        logger.trace(f'Mocking send and wait: {message}')
        self.send(message)
        self._motion_done.wait()

    def killalarm(self) -> None:
        logger.trace(f'Mocking killalarm')
        with self._lock:
            self._state = GrblMachineState.IDLE
            self._state_raw = "Idle"
        self._emit_state_update()

    def softreset(self) -> None:
        logger.trace(f'Mocking softreset')
        self.killalarm()

    def hold(self) -> None:
        logger.trace(f'Mocking hold')
        with self._lock:
            self._state = GrblMachineState.IDLE
            self._state_raw = "Hold:0"
        self._motion_done.set()
        self._emit_state_update()

    def get_position(self) -> CylindricalPosition:
        with self._lock:
            return CylindricalPosition(self._pos_r, self._pos_t, self._pos_z)

    def get_machine_position(self) -> CylindricalPosition:
        with self._lock:
            return CylindricalPosition(
                self._machine_pos_r,
                self._machine_pos_t,
                self._machine_pos_z,
            )

    def get_state(self) -> GrblMachineState:
        with self._lock:
            return self._state

    def get_state_raw(self) -> str:
        with self._lock:
            return self._state_raw

    def set_on_state_update_callback(self, callback) -> None:
        self._on_state_update_callback = callback
        self._emit_state_update()

    def force_position_update(self):
        logger.trace(f'Mocking force position update')
        self._emit_state_update()

    def _is_motion_command(self, message: str) -> bool:
        stripped = message.strip().upper()
        return stripped.startswith(("G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03", "$H"))

    def _target_from_message(self, message: str) -> Optional[tuple[float, float, float]]:
        with self._lock:
            return _target_from_message(
                message,
                self._pos_r,
                self._pos_t,
                self._pos_z,
            )

    def _start_motion(self, message: str) -> None:
        if message.strip().upper().startswith("$H"):
            target = (0.0, 0.0, 0.0)
        else:
            target = self._target_from_message(message)
            if target is None:
                self._emit_state_update()
                return

        self._motion_done.wait()
        with self._lock:
            start = (self._pos_r, self._pos_t, self._pos_z)
            self._state = GrblMachineState.RUN
            self._state_raw = "Run"
            self._motion_done.clear()

        duration = self._motion_duration(start, target)
        thread = threading.Thread(
            target=self._run_motion,
            args=(start, target, duration),
            name="GrblControllerMockStatus",
            daemon=True,
        )
        thread.start()

    def _motion_duration(self, start: tuple[float, float, float], target: tuple[float, float, float]) -> float:
        linear_delta = math.hypot(target[0] - start[0], target[2] - start[2])
        angular_delta = abs(target[1] - start[1])
        return max(
            linear_delta / self._linear_speed_mm_s,
            angular_delta / self._angular_speed_deg_s,
            self._status_interval_s,
        )

    def _run_motion(
        self,
        start: tuple[float, float, float],
        target: tuple[float, float, float],
        duration_s: float,
    ) -> None:
        started_at = time.monotonic()
        while not self._shutdown:
            elapsed = time.monotonic() - started_at
            fraction = min(1.0, elapsed / duration_s) if duration_s > 0 else 1.0
            pos = tuple(
                start_value + (target_value - start_value) * fraction
                for start_value, target_value in zip(start, target)
            )
            with self._lock:
                self._pos_r, self._pos_t, self._pos_z = pos
                self._machine_pos_r, self._machine_pos_t, self._machine_pos_z = pos
            self._emit_state_update()
            if fraction >= 1.0:
                break
            time.sleep(self._status_interval_s)

        with self._lock:
            self._pos_r, self._pos_t, self._pos_z = target
            self._machine_pos_r, self._machine_pos_t, self._machine_pos_z = target
            self._state = GrblMachineState.IDLE
            self._state_raw = "Idle"
        self._emit_state_update()
        self._motion_done.set()

    def _emit_state_update(self) -> None:
        callback = self._on_state_update_callback
        if callback is None:
            return
        position = self.get_position()
        machine_position = self.get_machine_position()
        state = self.get_state()
        try:
            try:
                callback(position, state, machine_position)
            except TypeError:
                callback(position, state)
        except Exception as exc:
            logger.error(f"Error in mock state update callback: {exc}")


class EventHandler:
    """
    Handles events from the GRBL streamer and maintains the current state and position.
    """
    def __init__(self, debug_serial: bool = False):
        """
        Initialize the EventHandler.
        """
        self._received_message = ''
        self._current_position = None
        self._machine_position = None

        self._state: GrblMachineState = GrblMachineState.IDLE
        self._state_raw: str = "Idle"

        self._on_state_update_callback = None
        self._debug_serial = debug_serial

    def set_on_state_update_callback(self, callback):
        """
        Set the callback function for state updates.

        :param callback: A callable that receives (position, state, machine_position).
        """
        self._on_state_update_callback = callback

    def get_received_message(self):
        """
        Get the last received message.

        :return: The received message string.
        """
        return self._received_message

    def set_received_message(self, value):
        """
        Set the received message.

        :param value: The message string.
        """
        self._received_message = value

    def get_current_position(self) -> CylindricalPosition:
        """
        Get the current cylindrical position.

        :return: A CylindricalPosition object.
        """
        return self._current_position

    def get_machine_position(self) -> Optional[CylindricalPosition]:
        """
        Get the current machine-coordinate cylindrical position.

        :return: A CylindricalPosition object, or None if not reported yet.
        """
        return self._machine_position

    def get_state(self) -> GrblMachineState:
        """
        Get the current machine state.

        :return: A GrblMachineState enum value.
        """
        return self._state

    def get_state_raw(self) -> str:
        """
        Get the raw state string.

        :return: The raw state string.
        """
        return self._state_raw

    def on_grbl_event(self, event, *data) -> None:
        """
        Callback for GRBL events from the streamer.

        :param event: The event name.
        :param data: Additional data associated with the event.
        """
        if getattr(self, "_debug_serial", False):
            logger.debug("GRBL serial event: event={!r} data={!r}", event, data)

        if event == "on_rx_buffer_percent":
            self._received_message = 'ok'
            if self._debug_serial:
                logger.debug("GRBL serial ACK inferred from rx buffer event")
        if event == "on_stateupdate":
            if len(data) >= 3:
                self._state_raw = str(data[0])
                self._state = GrblMachineState.from_grbl_mode(data[0])

                if isinstance(data[1], tuple):
                    mpos = data[1]
                    self._machine_position = CylindricalPosition(
                        mpos[1], mpos[2], mpos[0]
                    )

                if isinstance(data[2], tuple):
                    wpos = data[2]
                    self._current_position = CylindricalPosition(wpos[1], wpos[2], wpos[0])

                if self._on_state_update_callback:
                    try:
                        try:
                            self._on_state_update_callback(
                                self._current_position,
                                self._state,
                                self._machine_position,
                            )
                        except TypeError:
                            self._on_state_update_callback(
                                self._current_position,
                                self._state,
                            )
                    except Exception as e:
                        logger.error(f"Error in state update callback: {e}")

        if event == 'on_error':
            args = []
            for d in data:
                args.append(str(d))
            logger.error("ERROR: event={} data={}".format(event.ljust(30), ", ".join(args)))
            raise Exception("ERROR: event={} data={}".format(event.ljust(30), ", ".join(args)))

        if event == 'on_alarm':
            self._received_message = 'ok'
            self._state_raw = "Alarm"
            self._state = GrblMachineState.ALARM
            logger.error("ERROR: Alarm!")

        args = []
        for d in data:
            args.append(str(d))
        logger.trace("MY CALLBACK: event={} data={}".format(event.ljust(30), ", ".join(args)))


class GrblControllerFactory:
    """
    Creates instances of GRBL controller types based on configuration.

    This class provides factory methods to create various GRBL controllers, such as
    Arduino, ESP32Duino, and Mock controllers, using a configuration file. It handles
    the parsing of the configuration and the instantiation of the appropriate controller
    class based on the type specified in the configuration file. Additional methods
    assist in configuring specific GRBL controller settings like axes configurations.
    """
    _instance: Optional[IGrblController] = None
    _instance_signature: Optional[tuple] = None

    @staticmethod
    def reset(shutdown: bool = True) -> None:
        if shutdown and GrblControllerFactory._instance is not None:
            try:
                GrblControllerFactory._instance.shutdown()
            except Exception as exc:
                logger.warning(f"Failed to shut down cached GRBL controller: {exc}")
        GrblControllerFactory._instance = None
        GrblControllerFactory._instance_signature = None

    @staticmethod
    def _signature(
        config_parser: configparser.ConfigParser,
        section: str,
        type_to_build: str,
    ) -> tuple:
        if type_to_build == "Arduino":
            port = None
            if sys.platform.startswith("win32"):
                port = config_parser.get("windows", "port", fallback="")
            elif sys.platform.startswith("linux"):
                port = config_parser.get("linux", "port", fallback="")
            return (
                section,
                type_to_build,
                port,
                config_parser.getint(section, "baudrate", fallback=115200),
                config_parser.getboolean(
                    "scanner",
                    "verify_controller_on_connect",
                    fallback=True,
                ),
            )
        if type_to_build in {"MockSimulatedDRO", "MockWithDRO"}:
            return (
                section,
                type_to_build,
                config_parser.getfloat(section, "mock_linear_speed_mm_s", fallback=500.0),
                config_parser.getfloat(section, "mock_angular_speed_deg_s", fallback=180.0),
                config_parser.getfloat(section, "mock_status_hz", fallback=5.0),
            )
        return (section, type_to_build)

    @staticmethod
    def create(section: str, config_file: str) -> IGrblController:
        """
        Create a GRBL controller based on the configuration.

        :param section: The configuration section to use.
        :param config_file: Path to the configuration file.
        :return: An instance of IGrblController.
        """
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        type_to_build = config_parser.get(section, 'type')
        signature = GrblControllerFactory._signature(
            config_parser,
            section,
            type_to_build,
        )

        if GrblControllerFactory._instance is not None:
            if GrblControllerFactory._instance_signature == signature:
                logger.info("Using existing GRBL controller instance.")
                return GrblControllerFactory._instance
            logger.info(
                "GRBL controller configuration changed; rebuilding controller."
            )
            GrblControllerFactory.reset(shutdown=True)

        if type_to_build == 'Arduino':
            config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
            config_parser.read(config_file)
            section = 'grbl_streamer'
            debug_serial = _debug_serial_comms_enabled(config_parser)

            event_handler = EventHandler(debug_serial=debug_serial)
            grbl_streamer = GrblStreamer(event_handler.on_grbl_event)

            port = None
            if sys.platform.startswith('win32'):
                port = config_parser.get('windows', 'port')
            elif sys.platform.startswith('linux'):
                port = config_parser.get('linux', 'port')

            baudrate = config_parser.getint(section, 'baudrate')
            verify_controller = config_parser.getboolean(
                'scanner',
                'verify_controller_on_connect',
                fallback=True,
            )

            grbl_streamer.setup_logging()
            if debug_serial:
                logger.info("GRBL serial debug enabled")
                logger.info("GRBL serial opening port={} baudrate={}", port, baudrate)
            grbl_streamer.cnect(port, baudrate)
            logger.info('Waiting for gbrl to initialize..')
            if debug_serial:
                logger.info("GRBL serial startup wait: 3.0s")
            time.sleep(3)
            grbl_streamer.incremental_streaming = True
            if debug_serial:
                logger.info("GRBL serial sending startup status mask command: $10=2")
            grbl_streamer.send_immediately("$10=2")  # Force the report format to match what we expect.

            connection = GrblStreamerClientConnection(grbl_streamer, event_handler, debug_serial=debug_serial)
            try:
                instance = ESP32Duino(
                    connection,
                    debug_serial=debug_serial,
                    verify_on_connect=verify_controller,
                )
            except Exception:
                try:
                    connection.close()
                except Exception as close_exc:
                    logger.warning(f"Failed to close GRBL connection after probe failure: {close_exc}")
                raise
            GrblControllerFactory._instance = instance
            GrblControllerFactory._instance_signature = signature
            return instance
        elif type_to_build == 'Mock':
            instance = GrblControllerMock()
            GrblControllerFactory._instance = instance
            GrblControllerFactory._instance_signature = signature
            return instance
        elif type_to_build in {'MockSimulatedDRO', 'MockWithDRO'}:
            instance = GrblControllerMockSimulatedDRO(
                linear_speed_mm_s=config_parser.getfloat(
                    section,
                    'mock_linear_speed_mm_s',
                    fallback=500.0,
                ),
                angular_speed_deg_s=config_parser.getfloat(
                    section,
                    'mock_angular_speed_deg_s',
                    fallback=180.0,
                ),
                status_hz=config_parser.getfloat(
                    section,
                    'mock_status_hz',
                    fallback=5.0,
                ),
            )
            GrblControllerFactory._instance = instance
            GrblControllerFactory._instance_signature = signature
            return instance
        else:
            raise Exception(f'Unknown controller type: {type_to_build}')


class GrblStreamerClientConnection:
    """
    Manages a connection to a GRBL streamer.
    """
    def __init__(
        self,
        grbl_streamer: GrblStreamer,
        event_handler: EventHandler,
        debug_serial: bool = False,
    ) -> None:
        """
        Initialize the connection.

        :param grbl_streamer: The GRBL streamer instance.
        :param event_handler: The event handler to use.
        """
        self._event_handler = event_handler
        self._grbl_streamer = grbl_streamer
        self._debug_serial = debug_serial

    def killalarm(self) -> None:
        """
        Send a killalarm command to the streamer.
        """
        logger.trace(f'GrblStreamerClientConnection: Sending message: killalarm')
        self._grbl_streamer.killalarm()

    def softreset(self) -> None:
        """
        Send a softreset command to the streamer.
        """
        logger.trace(f'GrblStreamerClientConnection: Sending message: softreset')
        self._grbl_streamer.softreset()

    def hold(self) -> None:
        """
        Send a hold command to the streamer.
        """
        logger.trace(f'GrblStreamerClientConnection: Sending message: hold')
        # somehow the grbl-streamer hold() function does not work with GRBLHAL
        self._grbl_streamer.send_immediately('!')
        
    def send(self, message: str) -> None:
        """
        Send a message to the streamer.

        :param message: The message string.
        """
        logger.trace(f'GrblStreamerClientConnection: Sending message: {message}')
        if getattr(self, "_debug_serial", False):
            logger.debug("GRBL serial TX: {!r}", message)
        self._grbl_streamer.send_immediately(message)

    def receive(self):
        """
        Receive the last message from the event handler.

        :return: The received message.
        """
        message = self._event_handler.get_received_message()
        self._event_handler.set_received_message('')
        if self._debug_serial and message:
            logger.debug("GRBL serial RX buffer: {!r}", message)
        return message

    def get_position(self) -> CylindricalPosition:
        """
        Get the current position from the event handler.

        :return: A CylindricalPosition object.
        """
        return self._event_handler.get_current_position()

    def get_machine_position(self) -> Optional[CylindricalPosition]:
        """
        Get the current machine-coordinate position from the event handler.

        :return: A CylindricalPosition object, or None if not reported yet.
        """
        return self._event_handler.get_machine_position()

    def get_state(self) -> GrblMachineState:
        """
        Get the current state from the event handler.

        :return: A GrblMachineState enum value.
        """
        return self._event_handler.get_state()

    def get_state_raw(self) -> str:
        """
        Get the raw state from the event handler.

        :return: The raw state string.
        """
        return self._event_handler.get_state_raw()

    def set_on_state_update_callback(self, callback) -> None:
        """
        Set the state update callback on the event handler.

        :param callback: The callback function.
        """
        self._event_handler.set_on_state_update_callback(callback)

    def close(self) -> None:
        """
        Close the connection.
        """
        self._grbl_streamer.disconnect()


class ESP32Duino(IGrblController):
    """
    Provides an implementation of the ESP32Duino controller for managing FluidNC-based
    CNC machines.

    The `ESP32Duino` class is a subclass of `IGrblController` and provides methods for
    communicating with and controlling a FluidNC CNC machine. It includes features for
    sending and receiving messages, handling position updates, and ensuring proper
    connection management.

    :ivar UNLOCK_COMMAND: Command used to unlock and clear any alarm state.
    :type UNLOCK_COMMAND: str

    :ivar _connection: Represents the underlying connection for communication with
        the CNC controller.
    :type _connection: ClientConnection
    """
    UNLOCK_COMMAND = "$X"  # Command to unlock and clear any alarm
    PROBE_ACK_TIMEOUT_S = 3.0

    def __init__(
        self,
        connection: GrblStreamerClientConnection,
        debug_serial: bool = False,
        verify_on_connect: bool = True,
    ) -> None:
        """
        Initialize the ESP32Duino controller.

        :param connection: The connection instance to use.
        """
        self._connection = connection
        self._debug_serial = debug_serial
        if verify_on_connect:
            self._unlock()
        else:
            logger.warning(
                "GRBL controller verification on connect is disabled; "
                "the serial port is open but controller communication is unverified"
            )

    def _unlock(self) -> None:
        """Initialize the connection by unlocking and clearing the buffer."""
        if getattr(self, "_debug_serial", False):
            logger.info("GRBL serial probing controller with {}", self.UNLOCK_COMMAND)
        self.send(self.UNLOCK_COMMAND, ack_timeout_s=self.PROBE_ACK_TIMEOUT_S)

    def shutdown(self) -> None:
        """
        Logs the shutdown process and closes the connection.

        This method ensures a clean shutdown by logging the disconnection process
        and properly closing the established connection.

        :return: None
        """
        logger.info('Disconnecting from GRBL device')
        self._connection.close()

    def send(self, message: str, ack_timeout_s: Optional[float] = None) -> None:
        """
        Send a G-code command and wait for acknowledgment.

        :param message: The command to send.
        :param ack_timeout_s: Optional timeout for startup/probe commands only.
        """
        self._connection.send(message + '\n')
        logger.trace(f'Sending message to GRBL device: {message}')
        if getattr(self, "_debug_serial", False):
            logger.debug(
                "GRBL serial waiting for ACK: command={!r} timeout_s={}",
                message,
                ack_timeout_s,
            )
        self._wait_for_ack(ack_timeout_s)

    def _send_immediate(self, message: str) -> None:
        """
        Send an immediate command (e.g., real-time commands).

        :param message: The command to send.
        """
        logger.trace(f'Sending immediate message to GRBL device: {message}')
        self._connection.send(message)

    def send_and_wait_for_move_ready(self, message: str) -> None:
        """
        Sends a message, waits for acknowledgment (sync point), 
        and then ensures we have a valid position.
        
        :param message: The movement command to send.
        """
        self.send(message)
        self.send('G04 P0')
        
        # During arcs, status reports might be blocked.
        # Since 'G4 P0' just returned 'ok', we know we are physically at the target.
        # We wait a brief moment for the 'Idle' report to catch up if it's lagging.
        self._wait_for_idle_state()

    def force_position_update(self) -> None:
        """
        Force a position update (currently just waits for push updates to catch up).
        """
        logger.trace('Forcing position update.')
        time.sleep(0.2)

    def _wait_for_idle_state(self) -> None:
        """
        Block until the machine state becomes IDLE.
        """
        while self._connection.get_state() != GrblMachineState.IDLE:
            time.sleep(0.2)

    def killalarm(self) -> None:
        """
        Send a killalarm command.
        """
        logger.trace(f'Sending killalarm GRBL device')
        self._connection.killalarm()

    def softreset(self) -> None:
        """
        Send a softreset command.
        """
        logger.trace(f'Sending softreset GRBL device')
        self._connection.softreset()

    def hold(self) -> None:
        """
        Send a hold command.
        """
        logger.trace(f'Sending hold GRBL device')
        self._connection.hold()

    def get_position(self) -> CylindricalPosition:
        """
        Get the current cylindrical position.

        :return: A CylindricalPosition object.
        """
        return self._connection.get_position()

    def get_machine_position(self) -> Optional[CylindricalPosition]:
        """
        Get the current machine-coordinate cylindrical position.

        :return: A CylindricalPosition object, or None if not reported yet.
        """
        return self._connection.get_machine_position()

    def get_state(self) -> GrblMachineState:
        """
        Get the current machine state.

        :return: A GrblMachineState enum value.
        """
        return self._connection.get_state()

    def get_state_raw(self) -> str:
        """
        Get the raw state string.

        :return: The raw state string.
        """
        return self._connection.get_state_raw()

    def set_on_state_update_callback(self, callback) -> None:
        """
        Set the state update callback.

        :param callback: The callback function.
        """
        self._connection.set_on_state_update_callback(callback)

    def _wait_for_ack(self, timeout_s: Optional[float] = None) -> None:
        """
        Wait until an 'ok' acknowledgment is received from the hardware.
        """
        deadline = time.monotonic() + timeout_s if timeout_s is not None else None
        if getattr(self, "_debug_serial", False):
            logger.debug("GRBL serial ACK wait started timeout_s={}", timeout_s)
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.01)
            result = self._receive().rstrip()
            if result != "":
                logger.trace(f'Received: {result}')
                if getattr(self, "_debug_serial", False):
                    logger.debug("GRBL serial ACK wait received: {!r}", result)
            if "ok" in result:
                if getattr(self, "_debug_serial", False):
                    logger.info("GRBL serial ACK received")
                return
        if getattr(self, "_debug_serial", False):
            logger.error("GRBL serial ACK timeout after {}s", timeout_s)
        raise TimeoutError(
            "No GRBL response received after opening the serial port"
        )

    def _receive(self) -> str:
        """
        Receive a message from the connection.
        :return: The decoded message string.
        """
        result = self._connection.receive()
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return result
