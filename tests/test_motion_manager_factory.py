import configparser
import os
from unittest.mock import Mock
import pytest
from nfs.motion_manager import (
    MotionManagerFactory,
    SafetyConfigurationError,
    CylindricalNoFlyZone,
    SphericalNoFlyZone,
    UNSAFE_POINT_ABORT,
    UNSAFE_POINT_SKIP,
)
from nfs.plugins.file_measurement_points import FileMeasurementPoints
from nfs import loader


@pytest.mark.parametrize('referenced', [False, True])
@pytest.mark.parametrize('valid_safety', [False, True])
@pytest.mark.parametrize('manager_type', [
    'CylindricalMeasurementMotionManager',
    'FastCylindricalMeasurementMotionManager',
    'SphericalMeasurementMotionManager',
])
def test_legacy_safe_radius_preserves_safety(tmp_path, referenced, valid_safety, manager_type):
    config_file = tmp_path / 'legacy.ini'
    config = configparser.ConfigParser()
    config['plugins'] = {'file': 'nfs.plugins.file_measurement_points'}
    config['motion_manager'] = {
        'type': manager_type, 'safe_radius': '500',
    }
    if valid_safety:
        config['motion_manager'].update({
            'no_fly_radius': '101', 'no_fly_z_min': '0', 'no_fly_z_max': '400',
        })
    points = {
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': str(tmp_path / 'points.csv'), 'safe_radius': '500',
    }
    if referenced:
        config['motion_manager']['measurement_points'] = 'points'
        config['points'] = points
    else:
        config['motion_manager'].update(points)
    with config_file.open('w') as stream:
        config.write(stream)
    original = config_file.read_bytes()
    loader.load_plugins(str(config_file), 'plugins')
    scanner = Mock()
    if valid_safety:
        for _ in range(2):
            manager = MotionManagerFactory.create(str(config_file), 'motion_manager', scanner)
            assert manager._no_fly_zone.retract_radius == 101
    else:
        with pytest.raises(SafetyConfigurationError) as error:
            MotionManagerFactory.create(str(config_file), 'motion_manager', scanner)
        assert str(config_file) in str(error.value)
        assert 'no_fly_radius' in str(error.value)
        assert 'motion_manager' in str(error.value)
    assert scanner.mock_calls == []
    assert config_file.read_bytes() == original


@pytest.mark.parametrize('options, expected_key', [
    ('measurement_points = missing_section\n', 'measurement_points'),
    ('unexpected_option = 12\n', 'unexpected_option'),
    ('no_fly_radius = -1\n', 'no_fly_radius'),
])
def test_configuration_diagnostics_identify_file_and_key(tmp_path, options, expected_key):
    config_file = tmp_path / 'project.ini'
    config_file.write_text(
        '[plugins]\nfile = nfs.plugins.file_measurement_points\n'
        '[motion_manager]\ntype = CylindricalMeasurementMotionManager\n'
        'measurement_points_type = FileMeasurementPoints\nfilename = absent.csv\n'
        'no_fly_z_min = 0\nno_fly_z_max = 400\n' + options,
        encoding='utf-8')
    loader.load_plugins(str(config_file), 'plugins')
    with pytest.raises(SafetyConfigurationError) as error:
        MotionManagerFactory.create(str(config_file), 'motion_manager', Mock())
    message = str(error.value)
    assert str(config_file) in message
    assert '[motion_manager]' in message
    assert expected_key in message
    assert 'Back up' in message


def test_motion_manager_factory_direct_config(tmp_path):
    config_file = tmp_path / "test_config.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {
        'plugins': 'plugins'
    }
    config['plugins'] = {
        'plugin_1': 'nfs.plugins.file_measurement_points'
    }
    config['motion_manager'] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': '101.0',
        'no_fly_z_min': '0.0',
        'no_fly_z_max': '400.0',
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': 'jan_cylinder_grid1.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }
    with open(config_file, 'w') as f:
        config.write(f)
    
    loader.load_plugins(str(config_file), 'plugins')
    
    # We need a real jan_cylinder_grid1.csv because FileMeasurementPoints tries to open it
    grid_file = tmp_path / "jan_cylinder_grid1.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")
    
    # Change working directory to tmp_path so the filename in config matches
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        mm = MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
        
        assert mm.__class__.__name__ == 'CylindricalMeasurementMotionManager'
        assert isinstance(mm._measurement_points, FileMeasurementPoints)
        assert mm._no_fly_zone.retract_radius == 101.0
        assert mm._measurement_points.total_points() == 1
        # No policy configured -> defaults to the safest 'abort'.
        assert mm._unsafe_point_policy == UNSAFE_POINT_ABORT
    finally:
        os.chdir(old_cwd)


def test_motion_manager_factory_reads_unsafe_point_policy(tmp_path):
    config_file = tmp_path / "test_config_policy.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {'plugin_1': 'nfs.plugins.file_measurement_points'}
    config['motion_manager'] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': '101.0',
        'no_fly_z_min': '0.0',
        'no_fly_z_max': '400.0',
        'unsafe_point_policy': 'skip',
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': 'jan_cylinder_grid1_policy.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }
    with open(config_file, 'w') as f:
        config.write(f)

    loader.load_plugins(str(config_file), 'plugins')

    grid_file = tmp_path / "jan_cylinder_grid1_policy.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        mm = MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
        assert mm._unsafe_point_policy == UNSAFE_POINT_SKIP
    finally:
        os.chdir(old_cwd)


def test_motion_manager_factory_legacy_config(tmp_path):
    config_file = tmp_path / "test_config_legacy.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {
        'plugins': 'plugins'
    }
    config['plugins'] = {
        'plugin_1': 'nfs.plugins.file_measurement_points'
    }
    config['motion_manager'] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': '101.0',
        'no_fly_z_min': '0.0',
        'no_fly_z_max': '400.0',
        'measurement_points': 'cylindrical_grid'
    }
    config['cylindrical_grid'] = {
        'type': 'FileMeasurementPoints',
        'filename': 'jan_cylinder_grid1_legacy.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }
    with open(config_file, 'w') as f:
        config.write(f)
    
    loader.load_plugins(str(config_file), 'plugins')
    
    grid_file = tmp_path / "jan_cylinder_grid1_legacy.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")
    
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        mm = MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
        
        assert mm.__class__.__name__ == 'CylindricalMeasurementMotionManager'
        assert isinstance(mm._measurement_points, FileMeasurementPoints)
        assert mm._no_fly_zone.retract_radius == 101.0
        assert mm._measurement_points.total_points() == 1
    finally:
        os.chdir(old_cwd)


@pytest.mark.parametrize("missing_key", ["no_fly_radius", "no_fly_z_min", "no_fly_z_max"])
def test_motion_manager_factory_cylindrical_missing_safety_param(tmp_path, missing_key):
    config_file = tmp_path / "test_config_missing.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {'plugin_1': 'nfs.plugins.file_measurement_points'}
    
    mm_cfg = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': '101.0',
        'no_fly_z_min': '0.0',
        'no_fly_z_max': '400.0',
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': 'grid.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }
    del mm_cfg[missing_key]
    config['motion_manager'] = mm_cfg

    with open(config_file, 'w') as f:
        config.write(f)

    loader.load_plugins(str(config_file), 'plugins')
    grid_file = tmp_path / "grid.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        with pytest.raises(SafetyConfigurationError):
            MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
    finally:
        os.chdir(old_cwd)


@pytest.mark.parametrize("bad_val", ["none", "", "abc", "nan", "inf", "-inf"])
def test_motion_manager_factory_cylindrical_invalid_safety_param(tmp_path, bad_val):
    config_file = tmp_path / "test_config_invalid.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {'plugin_1': 'nfs.plugins.file_measurement_points'}
    config['motion_manager'] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': bad_val,
        'no_fly_z_min': '0.0',
        'no_fly_z_max': '400.0',
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': 'grid.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }

    with open(config_file, 'w') as f:
        config.write(f)

    loader.load_plugins(str(config_file), 'plugins')
    grid_file = tmp_path / "grid.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        with pytest.raises(SafetyConfigurationError):
            MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
    finally:
        os.chdir(old_cwd)


def test_motion_manager_factory_cylindrical_invalid_geometry(tmp_path):
    config_file = tmp_path / "test_config_geom.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {'plugin_1': 'nfs.plugins.file_measurement_points'}
    config['motion_manager'] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': '101.0',
        'no_fly_z_min': '400.0',
        'no_fly_z_max': '0.0',  # z_min >= z_max
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': 'grid.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }

    with open(config_file, 'w') as f:
        config.write(f)

    loader.load_plugins(str(config_file), 'plugins')
    grid_file = tmp_path / "grid.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        with pytest.raises(SafetyConfigurationError):
            MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
    finally:
        os.chdir(old_cwd)


def test_motion_manager_factory_spherical_missing_radius(tmp_path):
    config_file = tmp_path / "test_config_spherical.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {'plugin_1': 'nfs.plugins.file_measurement_points'}
    config['motion_manager'] = {
        'type': 'SphericalMeasurementMotionManager',
        'measurement_points_type': 'FileMeasurementPoints',
        'filename': 'grid.csv',
        'homing_gap': '0.0',
        'pole_gap': '0.0'
    }

    with open(config_file, 'w') as f:
        config.write(f)

    loader.load_plugins(str(config_file), 'plugins')
    grid_file = tmp_path / "grid.csv"
    with open(grid_file, 'w') as f:
        f.write("r_xy_mm,phi_deg,z_mm\n100,0,0\n")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mock_scanner = Mock()
        with pytest.raises(SafetyConfigurationError):
            MotionManagerFactory.create(str(config_file), 'motion_manager', mock_scanner)
    finally:
        os.chdir(old_cwd)


def test_cylindrical_no_fly_zone_validations():
    # Valid
    zone = CylindricalNoFlyZone(100.0, 0.0, 400.0)
    assert zone.r_wall == 100.0
    assert zone.z_min == 0.0
    assert zone.z_max == 400.0

    # Non-positive radius
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(0.0, 0.0, 400.0)
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(-10.0, 0.0, 400.0)

    # Inverted or equal z bounds
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(100.0, 400.0, 0.0)
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(100.0, 100.0, 100.0)

    # Non-finite values
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(float('nan'), 0.0, 400.0)
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(100.0, float('-inf'), 400.0)
    with pytest.raises(SafetyConfigurationError):
        CylindricalNoFlyZone(100.0, 0.0, float('inf'))


def test_spherical_no_fly_zone_validations():
    # Valid
    zone = SphericalNoFlyZone(100.0)
    assert zone.radius == 100.0

    # Non-positive radius
    with pytest.raises(SafetyConfigurationError):
        SphericalNoFlyZone(0.0)
    with pytest.raises(SafetyConfigurationError):
        SphericalNoFlyZone(-5.0)

    # Non-finite radius
    with pytest.raises(SafetyConfigurationError):
        SphericalNoFlyZone(float('nan'))
    with pytest.raises(SafetyConfigurationError):
        SphericalNoFlyZone(float('inf'))
