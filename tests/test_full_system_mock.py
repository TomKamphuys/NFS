import os
import pytest
import csv
from nfs.nfs import NearFieldScannerFactory
from nfs.scanner import ScannerFactory


def test_full_system_mock_integration():
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
    config_file = "tests/full_system_mock_config.ini"
    log_file = "tests/full_system_test_positions.csv"

    # Ensure log file doesn't exist from previous runs
    if os.path.exists(log_file):
        os.remove(log_file)

    try:
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
        # Cleanup
        if os.path.exists(log_file):
            os.remove(log_file)


if __name__ == "__main__":
    test_full_system_mock_integration()
