"""
Tests for the endpoint safety guard that prevents the arm from being driven to
a measurement point that lies *inside* the no-fly (keep-out) zone.

This is the safety net for the scenario where a target point itself sits in the
protected volume: without it the managers would trigger an evasive move and then
still drive straight into the device under test.
"""
from unittest.mock import Mock

import pytest

from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import (
    CylindricalMeasurementMotionManager,
    CylindricalNoFlyZone,
    FastCylindricalMeasurementMotionManager,
    SphericalNoFlyZone,
    UnsafeMeasurementPointError,
    UNSAFE_POINT_ABORT,
    UNSAFE_POINT_SKIP,
    normalize_unsafe_point_policy,
)


# --------------------------------------------------------------------------- #
# Zone.contains()
# --------------------------------------------------------------------------- #
def test_cylindrical_contains_interior_point():
    zone = CylindricalNoFlyZone(r_wall=100.0, z_min=0.0, z_max=400.0)
    assert zone.contains(CylindricalPosition(50.0, 0.0, 200.0)) is True


def test_cylindrical_wall_and_caps_are_safe():
    zone = CylindricalNoFlyZone(r_wall=100.0, z_min=0.0, z_max=400.0)
    # On the wall shell.
    assert zone.contains(CylindricalPosition(100.0, 0.0, 200.0)) is False
    # On the bottom cap.
    assert zone.contains(CylindricalPosition(50.0, 0.0, 0.0)) is False
    # On the top cap.
    assert zone.contains(CylindricalPosition(50.0, 0.0, 400.0)) is False




def test_spherical_contains_interior_and_surface():
    zone = SphericalNoFlyZone(100.0)
    assert zone.contains(CylindricalPosition(10.0, 0.0, 10.0)) is True
    # On the surface (length == radius) is safe.
    assert zone.contains(CylindricalPosition(100.0, 0.0, 0.0)) is False


# --------------------------------------------------------------------------- #
# policy normalization
# --------------------------------------------------------------------------- #
def test_normalize_policy_defaults_to_abort():
    assert normalize_unsafe_point_policy(None) == UNSAFE_POINT_ABORT
    assert normalize_unsafe_point_policy("nonsense") == UNSAFE_POINT_ABORT
    assert normalize_unsafe_point_policy(" SKIP ") == UNSAFE_POINT_SKIP


# --------------------------------------------------------------------------- #
# manager guard behaviour
# --------------------------------------------------------------------------- #
def _cyl_zone():
    return CylindricalNoFlyZone(r_wall=200.0, z_min=0.0, z_max=400.0)


@pytest.mark.parametrize(
    "manager_cls",
    [CylindricalMeasurementMotionManager, FastCylindricalMeasurementMotionManager],
)
def test_cylindrical_manager_aborts_on_interior_point(manager_cls):
    scanner = Mock()
    scanner.get_position.return_value = CylindricalPosition(200.0, 0.0, 0.0)
    points = Mock()
    points.next.return_value = CylindricalPosition(50.0, 0.0, 200.0)  # interior

    manager = manager_cls(scanner, points, _cyl_zone())

    with pytest.raises(UnsafeMeasurementPointError):
        manager.next()

    scanner.radial_move_to.assert_not_called()
    scanner.vertical_move_to.assert_not_called()
    scanner.planar_move_to.assert_not_called()


@pytest.mark.parametrize(
    "manager_cls",
    [CylindricalMeasurementMotionManager, FastCylindricalMeasurementMotionManager],
)
def test_cylindrical_manager_skips_interior_point(manager_cls):
    scanner = Mock()
    scanner.get_position.return_value = CylindricalPosition(200.0, 0.0, 0.0)
    points = Mock()
    unsafe = CylindricalPosition(50.0, 0.0, 200.0)     # interior -> skip
    safe = CylindricalPosition(200.0, 0.0, 200.0)      # on the wall -> ok
    points.next.side_effect = [unsafe, safe]
    points.ready.return_value = False

    manager = manager_cls(scanner, points, _cyl_zone(),
                          unsafe_point_policy=UNSAFE_POINT_SKIP)

    result = manager.next()

    assert result == safe
    assert points.next.call_count == 2
