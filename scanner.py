from loguru import logger
import configparser
from grbl_streamer import GrblStreamer  # type: ignore
from ticlib import TicUSB  # type: ignore
import time
import numpy as np


def has_intersect(plane_normal, ray_direction, epsilon=1e-6) -> bool:
    n_dot_u = plane_normal.dot(ray_direction)
    if abs(n_dot_u) < epsilon:
        return False

    return True


def line_plane_intersection(plane_normal, plane_point, ray_direction, ray_point) -> np.array:
    n_dot_u = plane_normal.dot(ray_direction)

    w = ray_point - plane_point
    si = -plane_normal.dot(w) / n_dot_u
    psi = w + si * ray_direction + plane_point
    return psi


class CylindricalPosition:
    def __init__(self, r, t, z):
        self._r = r
        self._t = t
        self._z = z

    def __eq__(self, other) -> bool:
        return (self.r(), self.t(), self.z()) == (other.r(), other.t(), other.z())

    def __str__(self) -> str:
        return f'({self.r()}, {self.t()}, {self.z()})'

    def r(self) -> float:
        return self._r

    def set_r(self, r: float) -> None:
        self._r = r

    def t(self) -> float:
        return self._t

    def set_t(self, t: float) -> None:
        self._t = t

    def z(self) -> float:
        return self._z

    def set_z(self, z: float) -> None:
        self._z = z


def cyl_to_cart(cylindrical_position):
    r = cylindrical_position.r()
    t = cylindrical_position.t()/180*np.pi
    z = cylindrical_position.z()

    x = r * np.cos(t)
    y = r * np.sin(t)
    z = z

    return x, y, z


def is_between(a, b, c):
    if a >= c:
        return a >= b >= c
    else:
        return a <= b <= c


def is_vertical_move_safe(current_position, next_position, plane_point_z):
    plane_normal = np.array([0, 0, 1])
    move_direction = np.array([0, 0, 1])
    plane_point = np.array([0, 0, plane_point_z])

    x, y, z = cyl_to_cart(current_position)
    move_point = np.array([x, y, z])

    intersection_point = line_plane_intersection(plane_normal, plane_point, move_direction, move_point)

    intersect_is_during_move = is_between(current_position.z(), intersection_point[2], next_position)
    if abs(intersection_point[0]) < 270/2 and abs(intersection_point[1]) < 195/2 and intersect_is_during_move:  # TODO from config
        logger.info(f'Unsafe move requested from {x, y, z} [mm] to {x, y, next_position} [mm] (xyz). Reverting to evasive move')
        return False
    else:
        return True


def is_radial_move_safe(current_position, next_position):
    radius = np.sqrt((195/2)**2 + (270/2)**2)
    if np.abs(current_position.z()) <= 375/2 and next_position < radius:
        logger.info(f'Unsafe radial move requested. Reverting to evasive move')
        return False
    else:
        return True


class Grbl:
    def on_grbl_event(self, event, *data):
        logger.trace(event)
        if event == "on_rx_buffer_percent":
            logger.debug('Motion complete')
            self._ready = True
        args = []
        for d in data:
            args.append(str(d))
        logger.trace("MY CALLBACK: event={} data={}".format(event.ljust(30), ", ".join(args)))

    def __init__(self):
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read('config.ini')
        section = 'grbl_streamer'
        port = config_parser.get(section, 'port')
        baudrate = config_parser.getint(section, 'baudrate')

        grbl_streamer = GrblStreamer(self.on_grbl_event)  # TODO add useful callback
        grbl_streamer.cnect(port, baudrate)
        grbl_streamer.poll_start()
        grbl_streamer.incremental_streaming = True
        self._grbl_streamer = grbl_streamer

        self.send('$3=1')

        self._set_axis_according_to_config(config_parser, 'x')
        self._set_axis_according_to_config(config_parser, 'y')

        self.send('$1=255')
        self._ready = True

    def move_x_to(self, position: float) -> None:
        self.send_and_wait(f'G0 X{position}')

    def move_y_to(self, position: float) -> None:
        self.send_and_wait(f'G0 Y{position}')

    def move_z_to(self, position: float) -> None:
        self.send_and_wait(f'G0 Z{position}')

    def get_current_position(self):
        self._grbl_streamer.write('?')

    def send(self, message: str) -> None:
        logger.trace(f'Sending message to grbl: {message}')
        self._grbl_streamer.send_immediately(message)

    def send_and_wait(self, message: str) -> None:
        self._ready = False
        self.send(message)
        while not self._ready:
            time.sleep(0.1)

        self._ready = False
        self.send('G04 P0')
        while not self._ready:
            time.sleep(0.1)

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


class GrblAxis:
    def __init__(self, grbl: Grbl):
        self._grbl = grbl

    def _wait_until_move_ready(self) -> None:
        self.send('G4 P0')

    def send(self, message) -> None:
        self._grbl.send(message)

    def set_as_zero(self) -> None:
        self._grbl.send('G92 X0 Y0')
        self._grbl.send('$10=0')


class GrblXAxis(GrblAxis):
    def __init__(self, grbl):
        super().__init__(grbl)

    def move_to(self, position: float) -> None:
        self._grbl.move_x_to(position)
        super()._wait_until_move_ready()


class GrblYAxis(GrblAxis):
    def __init__(self, grbl):
        super().__init__(grbl)

    def move_to(self, position: float) -> None:
        self._grbl.move_y_to(position)
        super()._wait_until_move_ready()


class TicAxis:
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

    def get_position(self) -> float:
        return self._tic.get_current_position() / self._steps_per_degree


class TicFactory:
    @staticmethod
    def create(config_file: str) -> TicAxis:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        degree_per_step = config_parser.getfloat('tic', 'degree_per_step')
        large_gear_nr_of_teeth = config_parser.getint('tic', 'large_gear_nr_of_teeth')
        small_gear_nr_of_teeth = config_parser.getint('tic', 'small_gear_nr_of_teeth')
        stepper_step_size = config_parser.getint('tic', 'stepper_step_size')
        steps_per_degree = large_gear_nr_of_teeth / small_gear_nr_of_teeth * stepper_step_size / degree_per_step

        tic = TicUSB()

        return TicAxis(tic, steps_per_degree)


class Scanner:
    """The Scanner class controls all the motions of the Near Field Scanner

    It delegates the movement per axis to lower level controllers.

    """

    def __init__(self, radial_mover, angular_mover, vertical_mover, evasive_move_radius):
        self._radial_mover = radial_mover
        self._angular_mover = angular_mover
        self._vertical_mover = vertical_mover
        self._evasive_move_radius = evasive_move_radius
        self._cylindrical_position = CylindricalPosition(0, 0, 0)

    def move_to(self, position: CylindricalPosition) -> None:
        logger.info(f'Moving to {position}')

        self.angular_move_to(position.t())

        check1 = is_vertical_move_safe(self._cylindrical_position, position.z(), 375 / 2)
        check2 = is_vertical_move_safe(self._cylindrical_position, position.z(), -375 / 2)

        if check1 and check2:
            self.vertical_move_to(position.z())
        else:
            self.evasive_move_to(position)
            return

        if is_radial_move_safe(self._cylindrical_position, position.r()):
            self.radial_move_to(position.r())
        else:
            self.evasive_move_to(position)
            return

    def evasive_move_to(self, position: CylindricalPosition) -> None:
        logger.trace('Performing evasive move')
        self.angular_move_to(position.t())

        # Move radially out to a safe radius to do the vertical move
        self.radial_move_to(self._evasive_move_radius)

        # Now we can safely move up/down
        self.vertical_move_to(position.z())

        # And only now we can move to the radius we want to be
        self.radial_move_to(position.r())

    def radial_move_to(self, position: float) -> None:
        logger.trace(f'Radial move to {position}')
        if self.get_position().r() != position:
            self._radial_mover.move_to(position)
        self._cylindrical_position.set_r(position)

    def angular_move_to(self, position: float) -> None:
        logger.trace(f'Angular move to {position}')
        if self.get_position().t() != position:
            self._angular_mover.move_to(position)
        self._cylindrical_position.set_t(position)

    def vertical_move_to(self, position: float) -> None:
        logger.trace(f'Vertical move to {position}')

        if self.get_position().z() != position:
            self._vertical_mover.move_to(position)

        self._cylindrical_position.set_z(position)

    def rotate_counterclockwise(self, amount: float) -> None:
        self.angular_move_to(self._cylindrical_position.t() + amount)

    def rotate_clockwise(self, amount: float) -> None:
        self.rotate_counterclockwise(-amount)

    def move_out(self, amount: float) -> None:
        self.radial_move_to(self._cylindrical_position.r() + amount)

    def move_in(self, amount: float) -> None:
        self.move_out(-amount)

    def move_up(self, amount: float) -> None:
        self.vertical_move_to(self._cylindrical_position.z() + amount)

    def move_down(self, amount: float) -> None:
        self.move_up(-amount)

    def get_position(
            self) -> CylindricalPosition:  # TODO I would like to get the position from the lower level controllers...
        return self._cylindrical_position

    def set_as_zero(self) -> None:
        self._radial_mover.set_as_zero()
        self._angular_mover.set_as_zero()
        self._vertical_mover.set_as_zero()

    def shutdown(self) -> None:
        pass


class ScannerFactory:
    @staticmethod
    def create(config_file: str) -> Scanner:
        # grbl_streamer = GrblStreamerFactory().create(config_file)
        grbl = Grbl()  # (grbl_streamer)
        radial_mover = GrblYAxis(grbl)
        angular_mover = TicFactory().create(config_file)
        vertical_mover = GrblXAxis(grbl)

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        evasive_move_radius = config_parser.getfloat('scanner', 'evasive_move_radius')
        scanner = Scanner(radial_mover, angular_mover, vertical_mover, evasive_move_radius)

        return scanner
