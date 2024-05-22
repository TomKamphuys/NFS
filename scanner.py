from loguru import logger
import configparser
from grbl_streamer import GrblStreamer  # type: ignore
from ticlib import TicUSB  # type: ignore
import time


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


def my_callback(event_string, *data):
    args = []
    for d in data:
        args.append(str(d))
    logger.info("MY CALLBACK: event={} data={}".format(event_string.ljust(30),
                                                       ", ".join(args)))


class GrblStreamerFactory:
    def create(self, config_file: str) -> GrblStreamer:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'grbl_streamer'
        port = config_parser.get(section, 'port')
        baudrate = config_parser.getint(section, 'baudrate')

        grbl_streamer = GrblStreamer(my_callback)  # TODO add useful callback
        grbl_streamer.cnect(port, baudrate)
        grbl_streamer.poll_start()
        # grbl_streamer.job_new()
        # grbl_streamer.incremental_streaming = True

        # switch direction of X axis
        grbl_streamer.send_immediately('$3=1')  # TODO from config one day
        # grbl_streamer.job_run()
        # value = X*2^0 + Y*2^1 + Z*2^2, as can be seen below
        # Setting Value	Mask	    Invert X	Invert Y	Invert Z
        # 0	            00000000	N	        N	        N
        # 1	            00000001	Y	        N	        N
        # 2	            00000010	N	        Y	        N
        # 3	            00000011	Y	        Y	        N
        # 4	            00000100	N	        N	        Y
        # 5	            00000101	Y	        N	        Y
        # 6	            00000110	N	        Y	        Y
        # 7	            00000111	Y	        Y	        Y

        # 255 : steppers stay on
        # 25 : stepper turn off
        grbl_streamer.send_immediately('$1=25')

        self._set_axis_according_to_config(grbl_streamer, config_parser, 'x')
        self._set_axis_according_to_config(grbl_streamer, config_parser, 'y')

        return grbl_streamer

    @staticmethod
    def _set_axis_according_to_config(grbl_streamer: GrblStreamer, config_parser: configparser, axis: str):
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

        grbl_streamer.send_immediately(f'${100 + nr}={steps_per_millimeter}')
        grbl_streamer.send_immediately(f'${110 + nr}={maximum_rate}')
        grbl_streamer.send_immediately(f'${120 + nr}={acceleration}')


class Grbl:
    def on_grbl_event(self, event, *data):
        logger.trace(event)
        if event == "on_rx_buffer_percent":
            logger.info('Motion complete')
            self._ready = True
        args = []
        for d in data:
            args.append(str(d))
        print("MY CALLBACK: event={} data={}".format(event.ljust(30), ", ".join(args)))

    def __init__(self):  # , grbl_streamer: GrblStreamer):
        grbl_streamer = GrblStreamer(self.on_grbl_event)  # TODO add useful callback
        grbl_streamer.cnect('COM8', 115200)
        grbl_streamer.poll_start()
        grbl_streamer.incremental_streaming = True
        self._grbl_streamer = grbl_streamer
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
        logger.trace(f'Moving to {position}')

        self.radial_move_to(position.r())
        self.vertical_move_to(position.z())
        self.angular_move_to(position.t())

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

    def get_position(self) -> CylindricalPosition:  # TODO I would like to get the position from the lower level controllers...
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
