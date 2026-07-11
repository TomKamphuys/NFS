from types import SimpleNamespace

import numpy as np
import pytest


pytest.importorskip("pyvista")
pytest.importorskip("pyvistaqt")
pytest.importorskip("PySide6")

from grid_generator import coord_viewer_core_pyvista


def test_pyvista_robot_origin_uses_stl_z_zero_datum():
    viewer = SimpleNamespace(
        bounds=coord_viewer_core_pyvista.BoundsInfo(
            has_inner=True,
            has_outer=True,
            r_int=0.2,
            h_int=0.5,
            r_ext=0.25,
            h_ext=0.7,
            z_center=-0.15,
        ),
        N=2,
        z=np.array([-0.5, 0.2], dtype=float),
    )

    assert coord_viewer_core_pyvista.CoordViewerPyVista._robot_origin_z(viewer) == 0.0
