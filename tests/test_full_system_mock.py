import os
import pytest
import csv
from nfs.nfs import NearFieldScannerFactory
from nfs.scanner import ScannerFactory


def test_full_system_mock_integration(tmp_path):
    """
    Test a full measurement set using mock audio and mock GRBL controller.
    This verifies the integration between:
    - NearFieldScannerFactory
    - ScannerFactory
    - GrblControllerFactory (Mock)
    - AudioFactory (MockInterfaceAudio)
    - MotionManagerFactory
    - Plugin loader
    """
    # Change working directory to tmp_path for isolation
    old_cwd = os.getcwd()
    os.chdir(tmp_path)

    try:
        # 0. Copy the config file and ensure needed directories exist
        test_dir = os.path.dirname(os.path.abspath(__file__))
        original_config = os.path.join(test_dir, "full_system_mock_config.ini")
        config_file = "full_system_mock_config.ini"
        import shutil
        shutil.copy(original_config, config_file)

        log_file = "full_system_test_positions.csv"

        # 1. Create the scanner using the factory
        scanner = ScannerFactory.create(config_file)

        # 2. Create the NFS orchestrator
        # We pass the log_file path to verify it later
        nfs = NearFieldScannerFactory.create(scanner, config_file)
        nfs._position_log_file = log_file
        nfs._clear_position_log()  # re-clear with new path

        # 3. Run a full measurement set
        # With the provided config, this should take a few seconds as it processes a small set of points
        # NOTE: nfs.take_measurement_set() clears the log file at the START.
        # It should contain data after completion.
        nfs.take_measurement_set()

        # 4. Verify results
        assert os.path.exists(log_file), "Position log file was not created"

        with open(log_file, 'r') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        assert len(rows) > 0, "Position log file is empty"
        print(f"Successfully measured {len(rows)} points in mock integration test.")

        # Verify columns
        assert header == ['r_xy_mm', 'phi_deg', 'z_mm']

        # 5. Shutdown
        nfs.shutdown()

    finally:
        os.chdir(old_cwd)


if __name__ == "__main__":
    test_full_system_mock_integration()
