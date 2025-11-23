from unittest.mock import Mock, call

from src.nfs.scanner import PlanarMover


def test_cw_arc_move_to():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    z = 4.0
    r = 5.0
    radius = 6.0
    planar_mover.cw_arc_move_to(r, z, radius)

    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with(f'G02 X{z:.4f} Y{r:.4f} R{radius:.4f} F{feed_rate}')


def test_ccw_arc_move_to():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    z = 4.0
    r = 5.0
    radius = 6.0
    planar_mover.ccw_arc_move_to(r, z, radius)

    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with(f'G03 X{z:.4f} Y{r:.4f} R{radius:.4f} F{feed_rate}')


def test_move_to_rz():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    z = 4.000
    r = 5.000
    planar_mover.move_to_rz(r, z)

    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with(f'G0 X{z} Y{r}')


def test_move_to_vertical():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    z = 4.0
    planar_mover.move_to_vertical(z)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with(f'G0 X{z}')


def test_move_to_radial():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    r = 4.0
    planar_mover.move_to_radial(r)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with(f'G0 Y{r}')


def test_set_as_zero():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    planar_mover.set_as_zero()

    calls = [call('G92 X0 Y0'), call('$10=0')]
    mock_grbl.send.assert_has_calls(calls)


def test_shutdown():
    mock_grbl = Mock()

    feed_rate = 3.0
    planar_mover = PlanarMover(mock_grbl, feed_rate)
    planar_mover.shutdown()

    mock_grbl.shutdown.assert_called_once()
