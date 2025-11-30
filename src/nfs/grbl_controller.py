import configparser
import sys
import time
from abc import ABC, abstractmethod
from grbl_streamer import GrblStreamer  # type: ignore
from loguru import logger

from .client_connection import ClientConnectionFactory, IClientConnection
from .datatypes import GrblConfig, CylindricalPosition


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
    def softreset(self) -> None:
        pass

    @abstractmethod
    def send(self, message: str) -> None:
        pass

    @abstractmethod
    def send_and_wait_for_move_ready(self, message: str) -> None:
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

    def softreset(self) -> None:
        logger.trace(f'MockingShutting down')

    def send(self, message: str) -> None:
        logger.trace(f'Mocking sending message')

    def send_and_wait_for_move_ready(self, message: str) -> None:
        logger.trace(f'Mocking send and wait')


class GrblFileWriter(IGrblController):
    def shutdown(self) -> None:
        pass

    def softreset(self) -> None:
        pass

    def send(self, message: str) -> None:
        with open('grbl_file.gcode', 'a') as f:
            f.write(message + '\n')

    def send_and_wait_for_move_ready(self, message: str) -> None:
        with open('grbl_file.gcode', 'a') as f:
            f.write(message + '\n')
            f.write('G04 P0' + '\n')  # TODO this seems to be at the wrong level. Mock ClientConnection


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

    def __init__(self, connection: IClientConnection) -> None:
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
        waits for idle.

        :param message: The message to be sent.
        :type message: Str
        :return: None
        """
        self.send(message)
        self.send('G04 P0')

    def _wait_for_ack(self) -> None:
        """Wait until an 'ok' acknowledgment is received from the hardware."""
        ready = False
        while not ready:
            time.sleep(0.01)
            result = self._receive().rstrip()
            logger.trace(f'Received: {result}')
            if "ok" in result:
                ready = True

    def _receive(self) -> str:
        """Receive a message from the connection."""
        result = self._connection.receive()
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        return result


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
            return Arduino(config_file)
        elif type_to_build == 'ESP32Duino':
            client_controller_section = config_parser.get(section, 'client_controller')
            connection = ClientConnectionFactory.create(client_controller_section, config_file)
            esp32duino =  ESP32Duino(connection)

            x_section = config_parser.get(section, 'grbl_x_axis_config')
            grbl_config_x = GrblControllerFactory.create_grbl_config(x_section, config_parser)
            y_section = config_parser.get(section, 'grbl_y_axis_config')
            grbl_config_y = GrblControllerFactory.create_grbl_config(y_section, config_parser)

            GrblControllerFactory._set_axis_according_to_config(esp32duino, grbl_config_x, 'x')
            GrblControllerFactory._set_axis_according_to_config(esp32duino, grbl_config_y, 'y')

            # esp32duino.send('$/stepping/idle_ms=255')  # always on, so no drift of position TODO MPOT does not work for arduino
            # esp32duino.send('$Motors/Init')  # Trinamic drivers (e.g., TMC2209) need this.
            #
            # esp32duino.send('$CD=config.yaml')

            return esp32duino
        elif type_to_build == 'Mock':
            return GrblControllerMock()
        elif type_to_build == 'GrblFileWriter':
            return GrblFileWriter()
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
        pass # TODO MPOT this is also called for arduino, but does not work
        # prefix = f'$/axes/{axis}'
        #
        # direction_pin = 16 if axis == 'x' else 27
        # direction_pin_attribute = 'low' if grbl_config.invert_direction else 'high'
        #
        # esp32duino.send(f'{prefix}/steps_per_mm={grbl_config.steps_per_millimeter}')
        # esp32duino.send(f'{prefix}/max_rate_mm_per_min={grbl_config.maximum_rate}')
        # esp32duino.send(f'{prefix}/acceleration_mm_per_sec2={grbl_config.acceleration}')
        # esp32duino.send(f'{prefix}/motor0/stepstick/direction_pin=gpio.{direction_pin}:{direction_pin_attribute}')


class Arduino(IGrblController):
    """
    Class representing an Arduino-based controller for interfacing with GRBL devices.

    This class is a specialized implementation that integrates with GRBL firmware to
    control CNC machines or similar devices. It handles configuration loading, GRBL
    streaming setup, and axis-specific settings. The class provides a set of methods
    to send commands to the GRBL device, wait for specific operations to complete,
    and shut down the connection cleanly. Logging is extensively used for debugging
    and monitoring events.

    :ivar _grbl_streamer: Instance of a GRBL streaming implementation, either a mock or
        an actual streamer depending on the configuration.
    :type _grbl_streamer: GrblStreamer or GrblStreamerMock
    :ivar _ready: Internal flag indicating whether the GRBL device is ready for the next
        command. Used for synchronous operations.
    :type _ready: Bool
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
        # TODO MPOT self._set_axis_according_to_config(config_parser, 'rot') rotation also has to be setup sometime via config

        self.send('$1=255')  # servo's always on
        self.send('$X')  # unlock  TODO MPOT added
        self.send('$3=2')  # TODO MPOT added to set axes direction ok, was changed somehow???
        self.send('$$')
        self._ready = True

    def softreset(self) -> None:
        logger.info('softreset')
        self._grbl_streamer.softreset()

    def shutdown(self) -> None:
        logger.info('Disconnecting from GRBL')
        self.softreset()
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
        pass
        # section = f'grbl_{axis}_axis'
        # steps_per_millimeter = config_parser.getfloat(section, 'steps_per_millimeter')
        # maximum_rate = config_parser.getfloat(section, 'maximum_rate')
        # acceleration = config_parser.getfloat(section, 'acceleration')
        #
        # nr = 0  # silence the code analyzer
        # if axis == 'x':
        #     nr = 0
        # elif axis == 'y':
        #     nr = 1
        # else:
        #     logger.critical('Unsupported axis in configuration file. Axis found is ' + axis)
        #
        # self.send(f'${100 + nr}={steps_per_millimeter}')
        # self.send(f'${110 + nr}={maximum_rate}')
        # self.send(f'${120 + nr}={acceleration}')


class GrblStreamerMock:
    """
    Mock implementation of a Grbl streamer.

    This class serves as a mock for testing purposes, simulating the behavior of
    a Grbl streamer without requiring actual hardware or network connections. It
    replicates key functionality such as connecting, sending messages, and
    handling events, with logging used to simulate and record operations. This
    is useful in testing environments where actual hardware-in-the-loop is not
    possible.
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
