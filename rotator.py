import configparser
import time
from abc import abstractmethod, ABC
from loguru import logger
from ticlib import TicUSB
from websocket import create_connection
from grbl_controller import IGrblController, ESP32Duino


class IRotator(ABC):
    @abstractmethod
    def move_to(self, position: float) -> None:
        pass

    @abstractmethod
    def set_as_zero(self) -> None:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass


class GrblRotator(IRotator):
    UNLOCK_COMMAND = "$X"  # Command to unlock and clear any alarm

    def __init__(self, grbl_controller: IGrblController, steps_per_degree: float):
        self._steps_per_degree = steps_per_degree
        self._current_angle = 0.0
        self._grbl_controller = grbl_controller
        logger.trace('init')

    def move_to(self, position: float) -> None:
        logger.trace('moveto')

        self._current_angle = position
        nr_of_steps = round(self._steps_per_degree * position)
        self._grbl_controller.send_and_wait(f'G0 X{nr_of_steps}')

    def set_as_zero(self) -> None:
        logger.trace('setaszero')

        self._grbl_controller.send('G92 X0 Y0')
        self._grbl_controller.send('$10=0')
        self._current_angle = 0.0

    def shutdown(self) -> None:
        self._grbl_controller.shutdown()


class TicRotator(IRotator):
    """
    Represents an axis controlled by a Tic motor controller.

    This class allows for movement control of a Tic-based axis, converting positions
    specified in degrees to motor steps based on a predefined steps-per-degree
    ratio. The class also provides methods to perform actions like setting the
    current position to zero and retrieving the current axis position in degrees.

    :ivar _steps_per_degree: Number of motor steps corresponding to a single degree
                             of movement.
    :type _steps_per_degree: int
    :ivar _tic: Instance of the Tic motor controller used to control the axis.
    :type _tic: Any
    """
    def __init__(self, tic, steps_per_degree):
        self._steps_per_degree = steps_per_degree
        self._tic = tic

    def move_to(self, position: float) -> None:
        self._tic.energize()
        self._tic.exit_safe_start()
        nr_of_steps = round(self._steps_per_degree * position)
        self._tic.set_target_position(nr_of_steps)
        self._wait_until_move_ready(nr_of_steps)
        self._tic.deenergize()

    def _wait_until_move_ready(self, nr_of_steps: int) -> None:
        while self._tic.get_current_position() != nr_of_steps:
            time.sleep(0.1)

    def set_as_zero(self) -> None:
        self._tic.halt_and_set_position(0)

    def shutdown(self) -> None:
        pass  # nothing to do here


class RotatorMock(IRotator):
    def __init__(self):
        pass

    def move_to(self, position: float) -> None:
        logger.trace(f'{position}')

    def set_as_zero(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class TicFactory:
    @staticmethod
    def create(config_file: str) -> IRotator:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'tic'

        mock = config_parser.getboolean(section, 'mock')
        if mock:
            return RotatorMock()

        degree_per_step = config_parser.getfloat(section, 'degree_per_step')
        large_gear_nr_of_teeth = config_parser.getint(section, 'large_gear_nr_of_teeth')
        small_gear_nr_of_teeth = config_parser.getint(section, 'small_gear_nr_of_teeth')
        stepper_step_size = config_parser.getint(section, 'stepper_step_size')
        steps_per_degree = large_gear_nr_of_teeth / small_gear_nr_of_teeth * stepper_step_size / degree_per_step

        tic = TicUSB()

        return TicRotator(tic, steps_per_degree)


class RotatorControllerFactory:
    @staticmethod
    def create(section: str, config_file: str) -> IRotator:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        type_to_build = config_parser.get(section, 'type')

        if type_to_build == 'TIC':
            return TicFactory.create(config_file)
        elif type_to_build == 'ESP32DuinoRotation':
            web_socket = config_parser.get(section, 'web_socket')
            connection = create_connection(web_socket)
            degree_per_step = config_parser.getfloat(section, 'degree_per_step')
            large_gear_nr_of_teeth = config_parser.getint(section, 'large_gear_nr_of_teeth')
            small_gear_nr_of_teeth = config_parser.getint(section, 'small_gear_nr_of_teeth')
            stepper_step_size = config_parser.getint(section, 'stepper_step_size')
            steps_per_degree = large_gear_nr_of_teeth / small_gear_nr_of_teeth * stepper_step_size / degree_per_step
            return GrblRotator(ESP32Duino(connection), steps_per_degree)
        else:
            raise Exception(f'Unknown controller type: {type}')