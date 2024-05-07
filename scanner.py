from loguru import logger
import configparser
from grbl_streamer import GrblStreamer

# logger.remove(0)
logger.add('scanner.log', mode='w', level="TRACE")


class CylindricalPosition:
    def __init__(self, r, t, z):
        self.r = r
        self.t = t
        self.z = z

    def __eq__(self, other):
        return (self.r(), self.t(), self.z()) == (other.r(), other.t(), other.z())

    def r(self):
        return self.r

    def t(self):
        return self.t

    def z(self):
        return self.z


def my_callback(eventstring, *data):
    args = []
    for d in data:
        args.append(str(d))
    logger.info("MY CALLBACK: event={} data={}".format(eventstring.ljust(30),
                                                       ", ".join(args)))

class GrblStreamerFactory:
    def create(self, config_file):
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        # grbl_streamer_config = config_parser.get('grbl', 'grbl_streamer')
        grbl_streamer_settings = config_parser.get('grbl', 'grbl_streamer_settings')
        port = config_parser.get('grbl_streamer', 'port')
        baudrate = config_parser.getint('grbl_streamer', 'baudrate')

        grbl_streamer = GrblStreamer(my_callback)  # TODO add usefull callback
        grbl_streamer.cnect(port, baudrate)
        grbl_streamer.poll_start()

        self._set_axis_according_to_config(grbl_streamer, config_parser, 'x')
        self._set_axis_according_to_config(grbl_streamer, config_parser, 'y')

    def _set_axis_according_to_config(self, grbl_streamer, config_parser, axis):
        section = f'grbl_{axis}_axis'
        steps_per_millimeter = config_parser.get(section, 'steps_per_millimeter')
        maximum_rate = config_parser.get(section, 'maximum_rate')
        acceleration = config_parser.get(section, 'acceleration')

        extra = 0  # silence the code analyzer
        if axis == 'x':
            extra = 0
        elif axis == 'y':
            extra = 1
        else:
            logger.critical('Unsupported axis in configuration file. Axis found is ' + axis)

        grbl_streamer.incremental_streaming(f'${100 + extra}={steps_per_millimeter}')
        grbl_streamer.incremental_streaming(f'${110 + extra}={maximum_rate}')
        grbl_streamer.incremental_streaming(f'${120 + extra}={acceleration}')


class GrblFactory:
    def create(self, config_file):
        grbl = Grbl(GrblFactory.create(config_file))


class Grbl:

    def __init__(self, grbl_streamer):
        self._grbl_streamer = grbl_streamer

    def move_x_to(self, position):
        # self._grbl_streamer.send_immediately()
        pass

    def move_y_to(self, position):
        pass

    def move_z_to(self, position):
        pass

    def get_current_position(self):
        self._grbl_streamer.incremental_streaming('?')


class GrblAxis:
    def __init__(self, grbl):
        self._grbl = grbl


class GrblXAxis(GrblAxis):
    def __init__(self, grbl):
        super().__init__(grbl)

    def move_to(self, position):
        self._grbl.move_x_to(position)


class GrblYAxis(GrblAxis):
    def __init__(self, grbl):
        super().__init__(grbl)

    def move_to(self, position):
        self._grbl.move_y_to(position)


class TicFactory:
    def create(self, config_file):
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        degree_per_step = config_parser.getfloat('tic', 'degree_per_step')
        large_gear_nr_of_teeth = config_parser.getint('tic', 'large_gear_nr_of_teeth')
        small_gear_nr_of_teeth = config_parser.getint('tic', 'small_gear_nr_of_teeth')
        stepper_step_size = config_parser.getint('tic', 'stepper_step_size')
        steps_per_degree = large_gear_nr_of_teeth / small_gear_nr_of_teeth * stepper_step_size / degree_per_step
        return TicAxis(steps_per_degree)


class TicAxis:
    def __init__(self, steps_per_degree):
        self._steps_per_degree = steps_per_degree

    def move_to(self, position):
        pass


class Scanner:
    def __init__(self, radial_mover, angular_mover, vertical_mover):
        self._radial_mover = radial_mover
        self._angular_mover = angular_mover
        self._vertical_mover = vertical_mover
        self._cylindrical_position = CylindricalPosition(0, 0, 0)

    def move_to(self, cylindrical_position):
        logger.trace('Moving to ...')
        self.radial_move_to(cylindrical_position.r())
        self.angular_move_to(cylindrical_position.t())
        self.vertical_move_to(cylindrical_position.z())

    def radial_move_to(self, position):
        logger.trace('Radial Move to ' + str(position))
        self._radial_mover.move_to(position)

    def angular_move_to(self, position):
        self._angular_mover.move_to(position)

    def vertical_move_to(self, position):
        self._vertical_mover.move_to(position)

    def get_position(self):
        return self._cylindrical_position
