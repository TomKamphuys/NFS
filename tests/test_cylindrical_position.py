import pytest
from src.nfs.datatypes import CylindricalPosition


def test_cylindrical_position_initialization():
    position = CylindricalPosition(3.0, 1.57, 5.0)
    assert position.r() == 3.0
    assert position.t() == 1.57
    assert position.z() == 5.0


def test_cylindrical_position_set_r():
    position = CylindricalPosition(1.0, 0.0, 0.0)
    position.set_r(4.5)
    assert position.r() == 4.5


def test_cylindrical_position_set_t():
    position = CylindricalPosition(1.0, 0.0, 0.0)
    position.set_t(3.14)
    assert position.t() == 3.14


def test_cylindrical_position_set_z():
    position = CylindricalPosition(1.0, 0.0, 0.0)
    position.set_z(2.0)
    assert position.z() == 2.0


def test_cylindrical_position_subtraction():
    pos1 = CylindricalPosition(10.0, 180.0, 5.0)
    pos2 = CylindricalPosition(2.0, 30.0, 1.0)
    result = pos1 - pos2
    assert result.r() == 8.0
    assert result.t() == 150.0
    assert result.z() == 4.0


def test_cylindrical_position_equality():
    position1 = CylindricalPosition(2.0, 1.0, 3.0)
    position2 = CylindricalPosition(2.0, 1.0, 3.0)
    position3 = CylindricalPosition(1.0, 1.0, 3.0)
    assert position1 == position2
    assert position1 != position3


def test_cylindrical_position_length():
    position = CylindricalPosition(3.0, 1.0, 4.0)
    assert position.length() == pytest.approx(5.0)


def test_cylindrical_position_str():
    position = CylindricalPosition(3.0, 1.57, 5.0)
    assert str(position) == "(3.0mm, 1.6°, 5.0mm)"
