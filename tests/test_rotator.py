from configparser import ConfigParser

import pytest
from rotator import calculate_steps_per_degree


def test_calculate_steps_per_degree_valid_input():
    config_parser = ConfigParser()
    section = 'TestSection'
    config_parser.add_section(section)
    config_parser.set(section, 'degree_per_step', '1.8')
    config_parser.set(section, 'large_gear_nr_of_teeth', '176')
    config_parser.set(section, 'small_gear_nr_of_teeth', '12')
    config_parser.set(section, 'stepper_step_size', '32')

    result = calculate_steps_per_degree(config_parser, section)

    assert result == pytest.approx(260.74, abs=0.01)


def test_calculate_steps_per_degree_invalid_section():
    config_parser = ConfigParser()
    section = 'NonExistentSection'
    with pytest.raises(Exception):
        calculate_steps_per_degree(config_parser, section)
