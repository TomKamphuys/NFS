import configparser
import time
from abc import abstractmethod, ABC

from loguru import logger
from ticlib import TicUSB  # type: ignore

from grbl_controller import IGrblController, GrblControllerFactory


class IRotator(ABC):
    """
    Interface for implementing a rotator mechanism.

    The IRotator class serves as an abstraction for devices or mechanisms
    that perform rotational movements. It defines the mandatory methods
    that any implementing class should provide to handle rotation, reset
    the rotational position, and shut down the mechanism.

    All methods in this interface must be implemented by a subclass
    to comply with the requirements for rotation control.

    """
    @abstractmethod
    def move_to(self, angle: float) -> None:
        pass

    @abstractmethod
    def set_as_zero(self) -> None:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass


class GrblRotator(IRotator):
    """
    GrblRotator is responsible for handling rotation operations using a Grbl controller.
    """
    UNLOCK_COMMAND = "$X"  # Unlock and clear any alarm
    ZERO_POSITION_COMMAND = "G92 X0 Y0"
    REPORT_POSITION_COMMAND = "$10=0"

    def __init__(self, grbl_controller: IGrblController, steps_per_degree: float):
        self._steps_per_degree = steps_per_degree
        self._grbl_controller = grbl_controller
        logger.trace('GrblRotator initialized')

    def move_to(self, angle: float) -> None:
        """
        Rotate to the specified angle.
        """
        steps = self._calculate_steps(angle)
        self._send_move_to_command(steps)

    def set_as_zero(self) -> None:
        """
        Reset the current position as zero.
        """
        self._send_reset_position_commands()

    def shutdown(self) -> None:
        """
        Safely shutdown the controller.
        """
        self._grbl_controller.shutdown()

    # Private helper methods
    def _calculate_steps(self, angle: float) -> int:
        """Calculate the number of motor steps needed to achieve the target angle."""
        return round(self._steps_per_degree * angle)

    def _send_move_to_command(self, steps: int) -> None:
        """Send move-to command to the GRBL controller."""
        logger.trace(f'Sending move-to command for {steps} steps')
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{steps}')

    def _send_reset_position_commands(self) -> None:
        """Send reset position-related commands to the GRBL controller."""
        logger.trace('Resetting position to zero')
        self._grbl_controller.send(self.ZERO_POSITION_COMMAND)
        self._grbl_controller.send(self.REPORT_POSITION_COMMAND)


class TicRotator(IRotator):
    """
    Provides functionality to control a TIC rotator to rotate to specific angles.

    This class is used to interact with a TIC device for managing angular
    rotations. The class calculates the number of steps required to achieve a
    desired rotation angle based on its steps-per-degree configuration. It
    interfaces with the TIC device to execute the rotational movement and
    provides methods for setting the zero position and properly shutting down
    the device.

    :ivar _steps_per_degree: Specifies the number of steps the motor takes per
        degree of rotation.
    :type _steps_per_degree: float
    :ivar _tic: Represents the TIC device used for handling rotations.
    :type _tic: Any
    """
    def __init__(self, tic, steps_per_degree):
        self._steps_per_degree = steps_per_degree
        self._tic = tic

    def move_to(self, angle: float) -> None:
        self._tic.energize()
        self._tic.exit_safe_start()
        nr_of_steps = round(self._steps_per_degree * angle)
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
    """
    Represents a mock implementation of the IRotator interface.

    This mock class is used for testing and simulation purposes. It provides
    implementations of the IRotator interface methods without performing any
    actual hardware-related operations. The main purpose of this mock is to
    allow testing of application logic that depends on a rotator without
    relying on external hardware.
    """
    def __init__(self):
        pass

    def move_to(self, angle: float) -> None:
        logger.trace(f'{angle}')

    def set_as_zero(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def calculate_steps_per_degree(config_parser, section):
    degree_per_step = config_parser.getfloat(section, 'degree_per_step')
    large_gear_nr_of_teeth = config_parser.getint(section, 'large_gear_nr_of_teeth')
    small_gear_nr_of_teeth = config_parser.getint(section, 'small_gear_nr_of_teeth')
    stepper_step_size = config_parser.getint(section, 'stepper_step_size')
    steps_per_degree = large_gear_nr_of_teeth / small_gear_nr_of_teeth * stepper_step_size / degree_per_step
    return steps_per_degree


class TicFactory:
    """
    A factory class for creating instances of IRotator using a configuration file.

    This class is responsible for reading a configuration file, extracting specific
    parameters, and constructing objects conforming to the IRotator interface. It
    utilizes the `configparser` module to parse the configuration and calculates the
    necessary parameters to initialize the rotator.
    """
    @staticmethod
    def create(config_file: str) -> IRotator:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'tic'

        steps_per_degree = calculate_steps_per_degree(config_parser, section)

        tic = TicUSB()

        return TicRotator(tic, steps_per_degree)


class RotatorFactory:
    """
    Factory class to create instances of different types of rotators.

    This class provides a static method to create rotator objects based on a
    configuration file and a specified section. It parses the configuration file
    to determine the type of rotator and constructs the appropriate object.
    """
    @staticmethod
    def create(section: str, config_file: str) -> IRotator:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        type_to_build = config_parser.get(section, 'type')

        if type_to_build == 'TIC':
            return TicFactory.create(config_file)

        if type_to_build == 'ESP32DuinoRotation':
            steps_per_degree = calculate_steps_per_degree(config_parser, section)

            grbl_config_section = config_parser.get(section, 'rotation_mover_controller')
            return GrblRotator(GrblControllerFactory.create(grbl_config_section, config_file), steps_per_degree)

        if type_to_build == 'Mock':
            return RotatorMock()

        raise Exception(f'Unknown controller type: {type}')
