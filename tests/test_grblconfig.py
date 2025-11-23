from src.nfs.datatypes import GrblConfig


def test_grbl_config_initialization():
    config = GrblConfig(steps_per_millimeter=100.0, maximum_rate=3000.0, acceleration=500.0, invert_direction=True)
    assert config.steps_per_millimeter == 100.0
    assert config.maximum_rate == 3000.0
    assert config.acceleration == 500.0
    assert config.invert_direction is True


def test_steps_per_millimeter_property():
    config = GrblConfig(steps_per_millimeter=200.0, maximum_rate=2500.0, acceleration=400.0, invert_direction=False)
    assert config.steps_per_millimeter == 200.0


def test_maximum_rate_property():
    config = GrblConfig(steps_per_millimeter=150.0, maximum_rate=2200.0, acceleration=450.0, invert_direction=False)
    assert config.maximum_rate == 2200.0


def test_acceleration_property():
    config = GrblConfig(steps_per_millimeter=120.0, maximum_rate=3100.0, acceleration=650.0, invert_direction=True)
    assert config.acceleration == 650.0


def test_invert_direction_property():
    config = GrblConfig(steps_per_millimeter=180.0, maximum_rate=2900.0, acceleration=600.0, invert_direction=False)
    assert config.invert_direction is False
