import configparser
import sys
import time
from abc import ABC, abstractmethod

from grbl_streamer import GrblStreamer  # type: ignore
from loguru import logger

from nfs.datatypes import CylindricalPosition, GrblMachineState


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
        pass

    @abstractmethod
    def send(self, message: str) -> None:
        pass

    @abstractmethod
    def send_and_wait_for_move_ready(self, message: str) -> None:
        pass

    @abstractmethod
    def killalarm(self) -> None:
        pass

    @abstractmethod
    def softreset(self) -> None:
        pass

    @abstractmethod
    def get_position(self) -> CylindricalPosition:
        pass

    @abstractmethod
    def get_state(self) -> GrblMachineState:
        pass

    @abstractmethod
    def get_state_raw(self) -> str:
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
        logger.trace(f'Mocking sending message')

    def send_and_wait_for_move_ready(self, message: str) -> None:
        logger.trace(f'Mocking send and wait')

    def killalarm(self) -> None:
        logger.trace(f'Mocking killalarm')

    def softreset(self) -> None:
        logger.trace(f'Mocking softreset')

    def get_position(self) -> CylindricalPosition:
        return CylindricalPosition(0.0, 0.0, 0.0)

    def get_state(self) -> GrblMachineState:
        return GrblMachineState.IDLE

    def get_state_raw(self) -> str:
        return "Idle"


class EventHandler:
    def __init__(self):
        self._received_message = ''
        self._current_position = CylindricalPosition(0.0, 0.0, 0.0)
        self._state = 'Idle'

    def get_received_message(self):
        return self._received_message

    def set_received_message(self, value):
        self._received_message = value

    def get_current_position(self) -> CylindricalPosition:
        return self._current_position

    def get_state(self) -> GrblMachineState:
        return self._state

    def get_state_raw(self) -> str:
        return self._state_raw

    def on_grbl_event(self, event, *data) -> None:
        if event == "on_rx_buffer_percent":
            self._received_message = 'ok'
            logger.trace("set OK", flush=True)
        if event == "on_stateupdate":
            # data[0]: mode ('Idle', 'Run', etc.)
            # data[1]: machine position tuple (m_x, m_y, m_z)
            # data[2]: working position tuple (w_x, w_y, w_z)
            if len(data) >= 3:
                self._state_raw = str(data[0])
                self._state = GrblMachineState.from_grbl_mode(data[0])

                if isinstance(data[2], tuple):
                    wpos = data[2]
                    # Mapping the tuple values to CylindricalPosition (r, t, z)
                    self._current_position = CylindricalPosition(wpos[1], wpos[2], wpos[0])

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
            logger.error("ERROR: Alarm!", flush=True)

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
    @staticmethod
    def create(section: str, config_file: str) -> IGrblController:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        type_to_build = config_parser.get(section, 'type')

        if type_to_build == 'Arduino':
            config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
            config_parser.read(config_file)
            section = 'grbl_streamer'

            event_handler = EventHandler()
            grbl_streamer = GrblStreamer(event_handler.on_grbl_event)

            port = None
            if sys.platform.startswith('win32'):
                port = config_parser.get('windows', 'port')
            elif sys.platform.startswith('linux'):
                port = config_parser.get('linux', 'port')

            baudrate = config_parser.getint(section, 'baudrate')

            grbl_streamer.setup_logging()
            grbl_streamer.cnect(port, baudrate)
            logger.info('Waiting for gbrl to initialize..')
            time.sleep(3)
            grbl_streamer.poll_start()
            grbl_streamer.incremental_streaming = True

            set_config = config_parser.getboolean(section, 'set_config')
            if set_config:
                # TODO read from config file
                GrblControllerFactory._set_axis_according_to_config(grbl_streamer, config_parser, 'x')
                GrblControllerFactory._set_axis_according_to_config(grbl_streamer, config_parser, 'y')
                GrblControllerFactory._set_axis_according_to_config(grbl_streamer, config_parser, 'z')

            connection = GrblStreamerClientConnection(grbl_streamer, event_handler)
            return ESP32Duino(connection)  # TODO MPOT check this. Maybe simply rename. Looking at the class diagram this can be much simpler and a few layers of indirection can be removed.
        elif type_to_build == 'Mock':
            return GrblControllerMock()
        else:
            raise Exception(f'Unknown controller type: {type}')

    @staticmethod
    def _set_axis_according_to_config(grbl_streamer, config_parser, axis: str) -> None:
        section = f'grbl_{axis}_axis'
        steps_per_millimeter = config_parser.getfloat(section, 'steps_per_millimeter')
        maximum_rate = config_parser.getfloat(section, 'maximum_rate')
        acceleration = config_parser.getfloat(section, 'acceleration')

        nr = 0  # silence the code analyzer
        if axis == 'x':
            nr = 0
        elif axis == 'y':
            nr = 1
        elif axis == 'z':
            nr = 2
        else:
            logger.critical('Unsupported axis in configuration file. Axis found is ' + axis)

        grbl_streamer.send_immediately(f'${100 + nr}={steps_per_millimeter}')
        grbl_streamer.send_immediately(f'${110 + nr}={maximum_rate}')
        grbl_streamer.send_immediately(f'${120 + nr}={acceleration}')


class GrblStreamerClientConnection:
    def __init__(self, grbl_streamer: GrblStreamer, event_handler: EventHandler) -> None:
        self._event_handler = event_handler
        self._grbl_streamer = grbl_streamer

    def killalarm(self) -> None:
        logger.trace(f'GrblStreamerClientConnection: Sending message: killalarm', flush=True)
        self._grbl_streamer.killalarm()

    def softreset(self) -> None:
        logger.trace(f'GrblStreamerClientConnection: Sending message: softreset', flush=True)
        self._grbl_streamer.softreset()

    def send(self, message: str) -> None:
        logger.trace(f'GrblStreamerClientConnection: Sending message: {message}', flush=True)
        self._grbl_streamer.send_immediately(message)

    def receive(self):
        message = self._event_handler.get_received_message()
        self._event_handler.set_received_message('')
        return message

    def get_position(self) -> CylindricalPosition:
        return self._event_handler.get_current_position()

    def get_state(self) -> str:
        return self._event_handler.get_state()

    def close(self) -> None:
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

    :ivar _position: Stores the current position of the CNC tool head in cylindrical
        coordinates.
    :type _position: CylindricalPosition
    """
    UNLOCK_COMMAND = "$X"  # Command to unlock and clear any alarm

    def __init__(self, connection: GrblStreamerClientConnection) -> None:
        self._connection = connection
        self._unlock()

    def _unlock(self) -> None:
        """Initialize the connection by unlocking and clearing the buffer."""
        self.send(self.UNLOCK_COMMAND)

    def shutdown(self) -> None:
        """
        Logs the shutdown process and closes the connection.

        This method ensures a clean shutdown by logging the disconnection process
        and properly closing the established connection.

        :raises Exception: If the connection is already closed or invalid during the
            shutdown process.
        :return: None
        """
        logger.info('Disconnecting from GRBL device')
        self._connection.close()

    def send(self, message: str) -> None:
        self._connection.send(message + '\n')
        logger.trace(f'Sending message to GRBL device: {message}')
        self._wait_for_ack()

    def _send_immediate(self, message: str) -> None:
        logger.trace(f'Sending immediate message to GRBL device: {message}')
        self._connection.send(message)

    def send_and_wait_for_move_ready(self, message: str) -> None:
        """
        Sends a message, waits for acknowledgment (sync point), 
        and then ensures we have a valid position.
        """
        self.send(message)
        self.send('G04 P0')
        
        # During arcs, status reports might be blocked.
        # Since 'G4 P0' just returned 'ok', we know we are physically at the target.
        # We wait a brief moment for the 'Idle' report to catch up if it's lagging.
        self._wait_for_idle_state()

    def _wait_for_idle_state(self) -> None:
        """
        Wait until the background event handler confirms the state is 'Idle'.
        If no update arrives (common in arcs), we force a status poll.
        """
        # Increase timeout for long arc moves
        timeout = time.time() + 5.0
        while self._connection.get_state() != GrblMachineState.IDLE and time.time() < timeout:
            # If we're not getting updates, sending a '?' can sometimes
            # nudge GRBL to send a status report.
            if int(time.time() * 10) % 5 == 0:  # Every 0.5s
                self._send_immediate('?')
            time.sleep(0.05)

    def killalarm(self) -> None:
        logger.trace(f'Sending killalarm GRBL device')
        self._connection.killalarm()

    def softreset(self) -> None:
        logger.trace(f'Sending softreset GRBL device')
        self._connection.softreset()

    def get_position(self) -> CylindricalPosition:
        return self._connection.get_position()

    def get_state(self) -> GrblMachineState:
        return self._connection.get_state()

    def get_state_raw(self) -> str:
        return self._connection.get_state_raw()

    def _wait_for_ack(self) -> None:
        """Wait until an 'ok' acknowledgment is received from the hardware."""
        ready = False
        while not ready:
            time.sleep(0.01)
            result = self._receive().rstrip()
            if result != "":
                logger.trace(f'Received: {result}')
            if "ok" in result:
                ready = True

    def _receive(self) -> str:
        """Receive a message from the connection."""
        result = self._connection.receive()
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return result