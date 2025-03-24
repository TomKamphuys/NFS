import configparser
import sys
import time
from abc import ABC, abstractmethod
from grbl_streamer import GrblStreamer  # type: ignore
from loguru import logger
from websocket import create_connection

from datatypes import GrblConfig, CylindricalPosition


class IGrblController(ABC):
    """
    Interface that defines the contract for a GRBL Controller.

    This class serves as an abstract base class (ABC) for creating GRBL controller
    implementations. It defines the essential methods needed for communication
    with GRBL devices, including shutting down the controller, sending messages,
    and sending messages while waiting for the machine to be ready for movement.
    All methods must be implemented by subclasses. The interface ensures consistent
    behavior in any derived GRBL controller implementation.

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
    def get_position(self):
        pass


class GrblControllerMock(IGrblController):
    """
    Mock implementation of IGrblController interface.

    This class is used for testing purposes to simulate the functionality
    of a Grbl controller without requiring actual hardware. It provides
    basic mocking capabilities for shutdown, sending messages, and sending
    messages while waiting for the system to be ready for the next move.
    """
    def shutdown(self) -> None:
        logger.trace(f'MockingShutting down')

    def send(self, message: str) -> None:
        logger.trace(f'Mocking sending message')

    def send_and_wait_for_move_ready(self, message: str) -> None:
        logger.trace(f'Mocking send and wait')

    def get_position(self):
        logger.trace(f'Mocking get_position')
        return CylindricalPosition(0, 0, 0)


class ESP32Duino(IGrblController):
    """
    Represents a controller for managing an ESP32-based FluidNC GRBL system.

    This class is a specialized implementation designed to control and manage an
    ESP32-based FluidNC GRBL controller through a given connection. It provides
    methods for communication, including sending messages, handling acknowledgments,
    and ensuring proper connection management during initialization and shutdown.

    :ivar UNLOCK_COMMAND: Command used to unlock and clear any existing alarm.
    :type UNLOCK_COMMAND: str
    :ivar _connection: The connection interface used for communication with the
        device.
    :type _connection: Any
    """
    UNLOCK_COMMAND = "$X"  # Command to unlock and clear any alarm

    def __init__(self, connection):
        self._position = CylindricalPosition(0, 0, 0)
        self._connection = connection
        self._unlock()

    def get_position(self):
        return self._position

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
        logger.info('Disconnecting from FluidNC')
        self._connection.close()

    def send(self, message: str) -> None:
        self._connection.send(message + '\n')
        logger.trace(f'Sending message to FluidNC: {message}')
        self._wait_for_ack()

    def _send_immediate(self, message: str) -> None:
        logger.trace(f'Sending immediate message to FluidNC: {message}')
        self._connection.send(message)

    def send_and_wait_for_move_ready(self, message: str) -> None:
        """
        Sends a message, waits for acknowledgment, sends a signal, and
        waits for another acknowledgment.

        :param message: The message to be sent.
        :type message: str
        :return: None
        """
        self.send(message)
        self._wait_for_idle()

    def _wait_for_idle(self) -> None:
        ready = False
        while not ready:
            self._send_immediate('?')
            time.sleep(0.2)
            result = self._receive()
            self._parse_position(result)
            logger.trace(f'Scanner is at: {self._position}')
            if "Idle" in result:
                ready = True

    def _parse_position(self, status: str) -> None:
        """
        Parses the machine status string to extract WPos (Work Position) coordinates.
    
        :param status: The status string to parse, e.g., '<Idle|WPos:147.982,249.409,-1333.558,-1333.558|FS:0,0>'.
        :return: A CylindricalPosition object representing the WPos values.
        """
        start = status.find(":") + 1

        if start < 6:
            return

        end = status.find("|", start)
        position = status[start:end].split(",")
        self._position = CylindricalPosition(float(position[0]), float(position[1]), float(position[2]))
        return

    def _wait_for_ack(self) -> None:
        """Wait until an 'ok' acknowledgment is received from the hardware."""
        ready = False
        while not ready:
            time.sleep(0.01)
            result = self._receive()
            if "ok" in result:
                ready = True

    def _receive(self) -> str:
        """Receive a message from the connection."""
        result = self._connection.recv()
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return result


class GrblControllerFactory:
    """
    Factory class to create instances of different types of GRBL controllers.

    This class provides static methods to create specific GRBL controller objects
    based on given configuration details. It reads the configuration from a file
    to determine the type of controller and its relevant settings. The supported
    controller types include Arduino, ESP32Duino, and Mock implementations. This
    class also provides methods to configure the axes of a controller according
    to the specified GRBL settings.
    """
    @staticmethod
    def create(section: str, config_file: str) -> IGrblController:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        type_to_build = config_parser.get(section, 'type')

        if type_to_build == 'Arduino':
            return Arduino(config_file)
        elif type_to_build == 'ESP32Duino':
            web_socket = config_parser.get(section, 'web_socket')
            connection = create_connection(web_socket)
            esp32duino =  ESP32Duino(connection)

            x_section = config_parser.get(section, 'grbl_x_axis_config')
            grbl_config_x = GrblControllerFactory.create_grbl_config(x_section, config_parser)
            y_section = config_parser.get(section, 'grbl_y_axis_config')
            grbl_config_y = GrblControllerFactory.create_grbl_config(y_section, config_parser)

            GrblControllerFactory._set_axis_according_to_config(esp32duino, grbl_config_x, 'x')
            GrblControllerFactory._set_axis_according_to_config(esp32duino, grbl_config_y, 'y')

            return esp32duino
        elif type_to_build == 'Mock':
            return GrblControllerMock()
        else:
            raise Exception(f'Unknown controller type: {type}')

    @staticmethod
    def create_grbl_config(section, config_parser) -> GrblConfig:
        steps_per_millimeter = config_parser.getfloat(section, 'steps_per_millimeter')
        maximum_rate = config_parser.getfloat(section, 'maximum_rate')
        acceleration = config_parser.getfloat(section, 'acceleration')
        invert_direction = config_parser.getboolean(section, 'invert_direction')
        return GrblConfig(steps_per_millimeter, maximum_rate, acceleration, invert_direction)

    @staticmethod
    def _set_axis_according_to_config(esp32duino: ESP32Duino, grbl_config: GrblConfig, axis: str) -> None:
        prefix = f'$/axes/{axis}/'
        esp32duino.send(f'{prefix}steps_per_mm={grbl_config.steps_per_millimeter}')
        esp32duino.send(f'{prefix}max_rate_mm_per_min={grbl_config.maximum_rate}')
        esp32duino.send(f'{prefix}acceleration_mm_per_sec2={grbl_config.acceleration}')
        esp32duino.send(f'{prefix}homing/positive_direction={grbl_config.invert_direction}')


class Arduino(IGrblController):
    """
    Represents an Arduino-based GRBL controller for managing CNC operations.

    The Arduino class is designed to interface with a GRBL-enabled CNC device. It supports
    initializing the GRBL device with configurations from a file, sending commands, and
    managing the status of the device. The class provides functionality for axis-specific
    settings, streaming commands, and handling GRBL events.

    :ivar _grbl_streamer: Streaming interface for GRBL device communication.
    :type _grbl_streamer: GrblStreamer or GrblStreamerMock
    :ivar _ready: Indicates whether the GRBL controller is ready to accept new commands.
    :type _ready: bool
    """
    def _on_grbl_event(self, event, *data) -> None:
        logger.trace(event)
        if event == "on_rx_buffer_percent":
            self._ready = True
        args = []
        for d in data:
            args.append(str(d))
        logger.trace("MY CALLBACK: event={} data={}".format(event.ljust(30), ", ".join(args)))

    def __init__(self, config_file):
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        section = 'grbl_streamer'

        mock = config_parser.getboolean(section, 'mock')
        if mock:
            grbl_streamer = GrblStreamerMock()
        else:
            grbl_streamer = GrblStreamer(self._on_grbl_event)

        port = 0
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
        self._grbl_streamer = grbl_streamer

        self.send('$3=1')

        self._set_axis_according_to_config(config_parser, 'x')
        self._set_axis_according_to_config(config_parser, 'y')

        self.send('$1=255')  # servo's always on
        self.send('$$')
        self._ready = True

    def shutdown(self) -> None:
        logger.info('Disconnecting from GRBL')
        self._grbl_streamer.disconnect()

    def send(self, message: str) -> None:
        logger.trace(f'Sending message to grbl: {message}')
        self._grbl_streamer.send_immediately(message)

    def send_and_wait_for_move_ready(self, message: str) -> None:
        self._ready = False
        self.send(message)
        while not self._ready:
            time.sleep(0.01)

        self._ready = False
        self.send('G04 P0')
        while not self._ready:
            time.sleep(0.01)

    def _set_axis_according_to_config(self, config_parser, axis: str) -> None:
        section = f'grbl_{axis}_axis'
        steps_per_millimeter = config_parser.getfloat(section, 'steps_per_millimeter')
        maximum_rate = config_parser.getfloat(section, 'maximum_rate')
        acceleration = config_parser.getfloat(section, 'acceleration')

        nr = 0  # silence the code analyzer
        if axis == 'x':
            nr = 0
        elif axis == 'y':
            nr = 1
        else:
            logger.critical('Unsupported axis in configuration file. Axis found is ' + axis)

        self.send(f'${100 + nr}={steps_per_millimeter}')
        self.send(f'${110 + nr}={maximum_rate}')
        self.send(f'${120 + nr}={acceleration}')


class GrblStreamerMock:
    """
    A mock class for simulating GRBL streamer behavior for testing purposes.

    This class provides a mocked implementation of the GRBL streaming interface,
    allowing users to simulate connections, commands, and events in controlled
    test environments. It is intended to aid in the development and testing of
    systems that interact with GRBL without requiring actual hardware.
    """
    def __init__(self):
        """
        Represents and initializes an instance of the class.

        This class is designed to manage and contain specific functionality related
        to the domain for which it is implemented. It offers mechanisms and
        features needed to achieve its purpose. The `__init__` method is used to
        initialize and prepare the class for use with its relevant attributes set
        to their default or provided states.

        """

    def setup_logging(self) -> None:
        logger.debug('mocked setup_logging')

    def cnect(self, port, baudrate) -> None:
        logger.debug(f'Mock cnect to {port} baudrate {baudrate}')

    def poll_start(self) -> None:
        logger.debug('Mock poll_start')

    def disconnect(self) -> None:
        logger.debug('Mock disconnect')

    def send_immediately(self, message) -> None:
        logger.debug(f'Mock send_immediately {message}')

    def write(self, message) -> None:
        logger.debug(f'Mock write {message}')

    def on_rx_buffer_percent(self, percent) -> None:
        logger.debug(f'Mock on_rx_buffer_percent {percent}')

    def on_grbl_event(self, event, *data) -> None:
        logger.debug(f'Mock on_grbl_event {event} {data}')

    def incremental_streaming(self, flag) -> None:
        logger.debug(f'Mock incremental_streaming {flag}')