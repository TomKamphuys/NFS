import configparser
import os
from unittest.mock import Mock
from nfs.motion_manager import MotionManagerFactory
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
        'safe_radius': '101.0',
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
        assert mm._safe_radius == 101.0
        assert mm._measurement_points.total_points() == 1
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
        'safe_radius': '101.0',
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
        assert mm._safe_radius == 101.0
        assert mm._measurement_points.total_points() == 1
    finally:
        os.chdir(old_cwd)
