from unittest.mock import Mock, patch
import pytest
import configparser
from nfs.nfs import NearFieldScannerFactory


@pytest.fixture
def mock_scanner():
    return Mock()


@pytest.fixture
def mock_config_file(tmp_path):
    config_file = tmp_path / "test_config.ini"
    config = configparser.ConfigParser()
    config['nfs'] = {
        'plugins': 'test_plugins',
        'audio': 'test_audio',
        'motion_manager': 'test_motion_manager'
    }
    with open(config_file, 'w') as f:
        config.write(f)
    return str(config_file)


def test_create_near_field_scanner(mock_scanner, mock_config_file):
    # We mock builtins.open to avoid the real file being read by config_parser.read(config_file) 
    # IN nfs.py, but actually we want it to read the tmp file we created.
    # The issue is likely that NearFieldScannerFactory.create() calls config_parser.read(config_file)
    # and if we mock open() globally it fails or returns empty.
    
    with patch("nfs.loader.load_plugins") as mock_load_plugins, \
         patch("nfs.audio.AudioFactory.create") as mock_audio_factory, \
         patch("nfs.motion_manager.MotionManagerFactory.create") as mock_motion_manager_factory, \
         patch("nfs.nfs.NearFieldScanner") as mock_near_field_scanner:
        
        mock_audio_instance = Mock()
        mock_mm_instance = Mock()
        mock_audio_factory.return_value = mock_audio_instance
        mock_motion_manager_factory.return_value = mock_mm_instance
        mock_nfs_instance = Mock()
        mock_near_field_scanner.return_value = mock_nfs_instance

        # We need to ensure that when NearFieldScanner is instantiated, 
        # its __init__ (which calls _clear_position_log and thus open()) 
        # doesn't fail if we are NOT mocking open. 
        # But here we mocked NearFieldScanner class itself, so its __init__ is not called.

        result = NearFieldScannerFactory.create(mock_scanner, mock_config_file)

        mock_load_plugins.assert_called_once_with(mock_config_file, 'test_plugins')
        mock_audio_factory.assert_called_once_with(mock_config_file, 'test_audio')
        mock_motion_manager_factory.assert_called_once_with(mock_config_file, 'test_motion_manager', mock_scanner)
        
        mock_near_field_scanner.assert_called_once_with(
            mock_scanner, mock_audio_instance, mock_mm_instance
        )

        assert result == mock_nfs_instance
