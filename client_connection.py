import configparser
from abc import ABC, abstractmethod

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


class RealClientConnection(IClientConnection):
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
        elif type_to_build == 'RealClientConnection':
            web_socket = config_parser.get(section, 'web_socket')
            connection = connect(web_socket, ping_interval=None)
            return RealClientConnection(connection)
        elif type_to_build == 'FileWriterClientConnection':
            return FileWriterClientConnection(config_parser.get(section, 'file_name'))
        else:
            raise Exception(f'Unknown controller type: {type}')
