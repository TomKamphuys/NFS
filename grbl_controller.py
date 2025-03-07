import configparser
import sys
import time
from abc import ABC, abstractmethod
from grbl_streamer import GrblStreamer
from loguru import logger
from websocket import create_connection


class IGrblController(ABC):
    @abstractmethod
    def shutdown(self):
        pass

    @abstractmethod
    def send(self, message: str) -> None:
        pass

    @abstractmethod
    def send_and_wait(self, message: str) -> None:
        pass


class GrblControllerMock(IGrblController):
    def shutdown(self):
        logger.trace(f'MockingShutting down')

    def send(self, message: str) -> None:
        logger.trace(f'Mocking sending message')

    def send_and_wait(self, message: str) -> None:
        logger.trace(f'Mocking send and wait')


class ESP32Duino(IGrblController):
    """
    The ESP32Duino class provides an interface for communication with the FluidNC
    controller hardware. It is designed to send and receive messages to control
    the connected hardware, including managing the initialization and shutdown
    operations.
    """
    UNLOCK_COMMAND = "$X"  # Command to unlock and clear any alarm

    def __init__(self, connection):
        self._connection = connection
        self._initialize_connection()

    def _initialize_connection(self) -> None:
        """Initialize the connection by unlocking and clearing the buffer."""
        self.send(self.UNLOCK_COMMAND)
        self._clear_initial_buffer()

    def _clear_initial_buffer(self) -> None:
        """Clear the buffer by calling _receive multiple times."""
        for _ in range(3):
            self._receive()

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
        """
        Sends a formatted message to the FluidNC system through an established connection.

        This method appends a newline character to the provided message and forwards
        it to the connected FluidNC system using the underlying connection.

        :param message: The message to be sent to the FluidNC system, as a string.
        :return: None
        """
        logger.info(f'Sending message to FluidNC: {message}')
        self._connection.send(message + '\n')

    def send_and_wait(self, message: str) -> None:
        """
        Sends a message, waits for acknowledgment, sends a signal, and
        waits for another acknowledgment.

        :param message: The message to be sent.
        :type message: str
        :return: None
        """
        self.send(message)
        self._wait_for_ack()
        self.send('G04P0')
        self._wait_for_ack()

    def _wait_for_ack(self) -> None:
        """Wait until an 'ok' acknowledgment is received from the hardware."""
        ready = False
        while not ready:
            time.sleep(0.1)
            result = self._receive()
            if "ok" in result:
                ready = True

    def _receive(self) -> str:
        """Receive a message from the connection."""
        result = self._connection.recv()
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        logger.trace(f'Result: {result.strip()}')
        return result


class GrblControllerFactory:
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
            return ESP32Duino(connection)
        elif type_to_build == 'Mock':
            return GrblControllerMock()
        else:
            raise Exception(f'Unknown controller type: {type}')


class Arduino(IGrblController):
    """
    Represents an interface to communicate with and configure a GRBL-based CNC controller.

    This class encapsulates the setup, interaction, and shutdown processes required to
    establish communication with a GRBL (G-code parser) controller. It supports processing
    configuration from a file, setting axis parameters, sending commands, and managing
    GRBL initialization and disconnection. It also handles events and provides methods
    to send commands synchronously or asynchronously.

    :ivar _grbl_streamer: Instance of the GRBL streamer object (either real or mock).
    :type _grbl_streamer: GrblStreamer or GrblStreamerMock
    :ivar _ready: Indicates if the system is ready to receive the next command.
    :type _ready: bool
    """
    def on_grbl_event(self, event, *data):
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
            grbl_streamer = GrblStreamer(self.on_grbl_event)

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

    def shutdown(self):
        logger.info('Disconnecting from GRBL')
        self._grbl_streamer.disconnect()

    def send(self, message: str) -> None:
        logger.trace(f'Sending message to grbl: {message}')
        self._grbl_streamer.send_immediately(message)

    def send_and_wait(self, message: str) -> None:
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
    def __init__(self):
        """
        Represents and initializes an instance of the class.

        This class is designed to manage and contain specific functionality related
        to the domain for which it is implemented. It offers mechanisms and
        features needed to achieve its purpose. The `__init__` method is used to
        initialize and prepare the class for use with its relevant attributes set
        to their default or provided states.

        """
        pass

    def setup_logging(self):
        logger.debug('mocked setup_logging')

    def cnect(self, port, baudrate):
        logger.debug(f'Mock cnect to {port} baudrate {baudrate}')

    def poll_start(self):
        logger.debug('Mock poll_start')

    def disconnect(self):
        logger.debug('Mock disconnect')

    def send_immediately(self, message):
        logger.debug(f'Mock send_immediately {message}')

    def write(self, message):
        logger.debug(f'Mock write {message}')

    def on_rx_buffer_percent(self, percent):
        logger.debug(f'Mock on_rx_buffer_percent {percent}')

    def on_grbl_event(self, event, *data):
        logger.debug(f'Mock on_grbl_event {event} {data}')

    def incremental_streaming(self, flag):
        logger.debug(f'Mock incremental_streaming {flag}')