from unittest.mock import Mock, patch

import pytest
from src.nfs.audio import IAudio
from src.nfs.nfs import NearFieldScannerFactory
from src.nfs.scanner import Scanner
from src.nfs.motion_manager import SphericalMeasurementMotionManager


@pytest.fixture
def mock_scanner():
    return Mock(spec=Scanner)


@pytest.fixture
def mock_audio():
    return Mock(spec=IAudio)


@pytest.fixture
def mock_motion_manager():
    return Mock(spec=SphericalMeasurementMotionManager)


@pytest.fixture
def mock_config_file():
    return "test_config.ini"


def test_create_near_field_scanner(mock_scanner, mock_config_file):
    with patch("nfs.loader.load_plugins") as mock_load_plugins, \
            patch("nfs.AudioFactory.create") as mock_audio_factory, \
            patch("nfs.factory.create") as mock_factory, \
            patch("nfs.SphericalMeasurementMotionManager") as mock_measurement_manager, \
            patch("nfs.NearFieldScanner") as mock_near_field_scanner:
        mock_audio_instance = Mock()
        mock_measurement_points = Mock()
        mock_audio_factory.return_value = mock_audio_instance
        mock_factory.return_value = mock_measurement_points

        result = NearFieldScannerFactory.create(mock_scanner, mock_config_file)

        mock_load_plugins.assert_called_once_with(mock_config_file)
        mock_audio_factory.assert_called_once_with(mock_config_file)
        mock_factory.assert_called_once()
        mock_measurement_manager.assert_called_once_with(mock_scanner, mock_measurement_points)
        mock_near_field_scanner.assert_called_once_with(
            mock_scanner, mock_audio_instance, mock_measurement_manager.return_value
        )

        assert result == mock_near_field_scanner.return_value


def test_create_raises_error_with_invalid_config_file(mock_scanner):
    invalid_config_file = "invalid_config.ini"
    with patch("nfs.loader.load_plugins") as mock_load_plugins, \
            patch("nfs.AudioFactory.create") as mock_audio_factory:
        mock_load_plugins.side_effect = FileNotFoundError("Config file not found")
        with pytest.raises(FileNotFoundError):
            NearFieldScannerFactory.create(mock_scanner, invalid_config_file)
        mock_load_plugins.assert_called_once_with(invalid_config_file)
        mock_audio_factory.assert_not_called()
