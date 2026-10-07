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
