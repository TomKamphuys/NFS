import configparser
from abc import ABC, abstractmethod
import time

from grbl_streamer import GrblStreamer
from websockets.sync.client import connect, ClientConnection


class IClientConnection(ABC):
    @abstractmethod
    def send(self, message: str) -> None:
        pass

    def receive(self) -> str:
        pass

    def close(self) -> None:
        pass


class MockClientConnection(IClientConnection):
    def send(self, message: str) -> None:
        print(f'MockClientConnection: Sending message: {message}')

    def receive(self) -> str:
        return 'MockClientConnection: Message received'

    def close(self) -> None:
        print('MockClientConnection: Disconnected')


class FileWriterClientConnection(IClientConnection):
    def __init__(self, file_name: str) -> None:
        self._file_name = file_name

    def send(self, message: str) -> None:
        with open(self._file_name, 'a') as f:
            f.write(message)

    def receive(self) -> str:
        return 'ok <Idle|MPos:320.000,320.000,320.000|FS:0.0,0>'

    def close(self) -> None:
        print('FileWriterClientConnection: Disconnected')


class WebSocketClientConnection(IClientConnection):
    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    def send(self, message: str) -> None:
        self._connection.send(message)
        with open('bla.gcode', 'a') as f:
            f.write(message)

    def receive(self):
        return self._connection.recv()

    def close(self) -> None:
        self._connection.close()


class GrblStreamerClientConnection(IClientConnection):
    def _on_grbl_event(self, event, *data) -> None:
        if event == "on_rx_buffer_percent":
            self._received_message = 'ok'
            print("set OK", flush=True)
        args = []
        for d in data:
            args.append(str(d))
        print("MY CALLBACK: event={} data={}".format(event.ljust(30), ", ".join(args)))

    def __init__(self) -> None:  # TODO from config some time
        self._received_message = ''
        grbl_streamer = GrblStreamer(self._on_grbl_event)
        grbl_streamer.setup_logging()
        grbl_streamer.cnect('COM8', 115200) # TODO MPOT should be from config
        # logger.info('Waiting for gbrl streamer to initialize...')
        time.sleep(3)
        # grbl_streamer.poll_start()
        grbl_streamer.incremental_streaming = True
        self._grbl_streamer = grbl_streamer
        print('GrblStreamerClientConnection: Connected', flush=True)

    def send(self, message: str) -> None:
        print(f'GrblStreamerClientConnection: Sending message: {message}', flush=True)
        self._grbl_streamer.send_immediately(message)

    def receive(self):
        message = self._received_message
        self._received_message = ''
        # print(f'GrblStreamerClientConnection: Received message: {message}', flush=True)
        return message

    def close(self) -> None:
        self._grbl_streamer.disconnect()


class ClientConnectionFactory:
    """
    Creates instances of GRBL controller types based on configuration.

    This class provides factory methods to create various GRBL controllers, such as
    Arduino, ESP32Duino, and Mock controllers, using a configuration file. It handles
    the parsing of the configuration and the instantiation of the appropriate controller
    class based on the type specified in the configuration file. Additional methods
    assist in configuring specific GRBL controller settings like axes configurations.
    """
    @staticmethod
    def create(section: str, config_file: str) -> IClientConnection:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        type_to_build = config_parser.get(section, 'type')

        if type_to_build == 'MockClientConnection':
            return MockClientConnection()
        elif type_to_build == 'WebSocketClientConnection':  # TODO websocket, add serial (now in Arduino). Pull apart Arduino/ESP32Duino and the way of communicating
            web_socket = config_parser.get(section, 'web_socket')
            connection = connect(web_socket, ping_interval=None)
            return WebSocketClientConnection(connection)
        elif type_to_build == 'GrblStreamerClientConnection':
            return GrblStreamerClientConnection()
        elif type_to_build == 'FileWriterClientConnection':
            return FileWriterClientConnection(config_parser.get(section, 'file_name'))
        else:
            raise Exception(f'Unknown controller type: {type}')
