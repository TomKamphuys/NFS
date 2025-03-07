from unittest.mock import Mock
from scanner import PlanarMover

def test_radial_move_to():
    mock_grbl = Mock()

    planar_mover = PlanarMover(mock_grbl)
    # TODO