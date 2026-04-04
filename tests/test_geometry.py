from nfs.datatypes import CylindricalPosition
from nfs.utils.geometry import cyl_to_cart
import numpy as np

def test_cylindrical_position_equality():
    position1 = CylindricalPosition(1.0, 2.0, 3.0)
    position2 = CylindricalPosition(1.0, 2.0, 3.0)
    position3 = CylindricalPosition(4.0, 5.0, 6.0)
    assert position1 == position2
    assert position1 != position3


def test_cylindrical_position_subtraction():
    pos1 = CylindricalPosition(10.0, 180.0, 5.0)
    pos2 = CylindricalPosition(2.0, 30.0, 1.0)
    result = pos1 - pos2
    assert result.r() == 8.0
    assert result.t() == 150.0
    assert result.z() == 4.0


def test_cylindrical_position_length():
    position = CylindricalPosition(3.0, 0.0, 4.0)
    assert position.length() == 5.0

def test_cyl_to_cart():
    position = CylindricalPosition(100.0, 90.0, 10.0)
    x, y, z = cyl_to_cart(position)
    assert np.isclose(x, 0.0)
    assert np.isclose(y, 100.0)
    assert np.isclose(z, 10.0)
