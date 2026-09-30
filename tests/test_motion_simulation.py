"""
Tests for :mod:`nfs.motion_simulation`, the GUI-free engine behind the optional
motion-preview window.

They verify that the off-line simulation:

* drives the *real*, configured motion manager over the recording scanner;
* reconstructs the **whole** path (many samples, not just the end points); and
* keeps the arm out of the configured no-fly zone -- the same safety invariant
  the manager itself guarantees, now re-checked through the preview pipeline.
"""
import configparser

from nfs import loader
from nfs.motion_simulation import simulate_motion, RecordingScanner
from nfs.datatypes import CylindricalPosition
from nfs.plugins.cylindrical_measurement_points import CylindricalMeasurementPoints


def _write_cylindrical_config(tmp_path):
    points = CylindricalMeasurementPoints(
        nr_of_angular_points=6,
        nr_of_radial_cap_points=3,
        nr_of_vertical_points=5,
        cap_spacing=10.0,
        wall_spacing=10.0,
        radius=300.0,
        height=400.0,
    )
    r_wall = points._radius - points._delta_radius
    z_min = points._cap_spacing
    z_max = points._height - points._cap_spacing

    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {
        'plugin_1': 'nfs.plugins.cylindrical_measurement_points',
    }
    config['motion_manager'] = {
        'type': 'FastCylindricalMeasurementMotionManager',
        'no_fly_radius': str(r_wall),
        'no_fly_z_min': str(z_min),
        'no_fly_z_max': str(z_max),
        'measurement_points_type': 'CylindricalMeasurementPoints',
        'nr_of_angular_points': '6',
        'nr_of_radial_cap_points': '3',
        'nr_of_vertical_points': '5',
        'cap_spacing': '10.0',
        'wall_spacing': '10.0',
        'radius': '300.0',
        'height': '400.0',
    }
    config_file = tmp_path / 'sim_config.ini'
    with open(config_file, 'w') as f:
        config.write(f)
    loader.load_plugins(str(config_file), 'plugins')
    return str(config_file), r_wall, z_min, z_max


def _write_cylindrical_config_custom_section(tmp_path, section_name):
    """Write a config whose motion-manager section is *not* named ``motion_manager``.

    ``[nfs] motion_manager`` points at ``section_name`` -- exactly how the running
    application resolves the section -- so this exercises that the preview follows
    the same reference instead of assuming the default section name.
    """
    r_wall, z_min, z_max = 290.0, 10.0, 390.0
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins', 'motion_manager': section_name}
    config['plugins'] = {
        'plugin_1': 'nfs.plugins.cylindrical_measurement_points',
    }
    config[section_name] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': str(r_wall),
        'no_fly_z_min': str(z_min),
        'no_fly_z_max': str(z_max),
        'measurement_points_type': 'CylindricalMeasurementPoints',
        'nr_of_angular_points': '6',
        'nr_of_radial_cap_points': '3',
        'nr_of_vertical_points': '5',
        'cap_spacing': '10.0',
        'wall_spacing': '10.0',
        'radius': '300.0',
        'height': '400.0',
    }
    config_file = tmp_path / 'sim_config.ini'
    with open(config_file, 'w') as f:
        config.write(f)
    loader.load_plugins(str(config_file), 'plugins')
    return str(config_file), r_wall, z_min, z_max


def _write_cylindrical_config_with_unsafe_points(tmp_path, unsafe_point_policy):
    """Write a config whose measurement grid intrudes into the no-fly zone.

    The no-fly wall radius is set *larger* than the measurement radius, so the
    wall measurement points (at the measurement radius, between the caps) fall
    inside the protected interior and must be reported as unsafe.
    """
    r_wall, z_min, z_max = 400.0, 10.0, 390.0
    config = configparser.ConfigParser()
    config['nfs'] = {'plugins': 'plugins'}
    config['plugins'] = {
        'plugin_1': 'nfs.plugins.cylindrical_measurement_points',
    }
    config['motion_manager'] = {
        'type': 'CylindricalMeasurementMotionManager',
        'no_fly_radius': str(r_wall),
        'no_fly_z_min': str(z_min),
        'no_fly_z_max': str(z_max),
        'unsafe_point_policy': unsafe_point_policy,
        'measurement_points_type': 'CylindricalMeasurementPoints',
        'nr_of_angular_points': '6',
        'nr_of_radial_cap_points': '3',
        'nr_of_vertical_points': '5',
        'cap_spacing': '10.0',
        'wall_spacing': '10.0',
        'radius': '300.0',
        'height': '400.0',
    }
    config_file = tmp_path / 'sim_config.ini'
    with open(config_file, 'w') as f:
        config.write(f)
    loader.load_plugins(str(config_file), 'plugins')
    return str(config_file), r_wall, z_min, z_max


def test_recording_scanner_samples_a_straight_move():
    scanner = RecordingScanner(CylindricalPosition(0.0, 0.0, 0.0))
    scanner.planar_move_to(100.0, 0.0)
    assert len(scanner.segments) == 1
    segment = scanner.segments[0]
    # A 100 mm move is sampled into several intermediate points, not just 2.
    assert len(segment.r) > 2
    assert segment.r[0] == 0.0
    assert segment.r[-1] == 100.0


def test_simulate_motion_reports_zone_and_full_path(tmp_path):
    config_file, r_wall, _z_min, _z_max = _write_cylindrical_config(tmp_path)
    sim = simulate_motion(config_file)

    assert sim.zone_kind == 'cylindrical'
    assert sim.zone['r_wall'] == r_wall
    assert sim.points, 'expected measurement points to be visited'
    assert sim.segments, 'expected recorded motion segments'

    path = sim.flatten()
    # The whole motion must contain many more samples than measurement points,
    # proving we render the full moves, not just the end points.
    assert len(path) > len(sim.points)


def test_simulate_motion_resolves_renamed_motion_manager_section(tmp_path):
    # The preview must follow ``[nfs] motion_manager`` just like the running app,
    # so a renamed section still yields the correct no-fly zone (previously the
    # hard-coded "motion_manager" section name meant nothing was drawn).
    config_file, r_wall, z_min, z_max = _write_cylindrical_config_custom_section(
        tmp_path, 'my_custom_motion'
    )
    sim = simulate_motion(config_file)

    assert sim.zone_kind == 'cylindrical'
    assert sim.zone['r_wall'] == r_wall
    assert sim.zone['z_min'] == z_min
    assert sim.zone['z_max'] == z_max
    assert sim.segments, 'expected recorded motion segments'


def test_simulate_motion_reports_no_unsafe_points_for_valid_grid(tmp_path):
    # A valid grid stays on/outside the zone, so nothing should be flagged.
    config_file, _r_wall, _z_min, _z_max = _write_cylindrical_config(tmp_path)
    sim = simulate_motion(config_file)

    assert sim.unsafe_points == []
    assert sim.aborted is False


def test_simulate_motion_flags_unsafe_points_and_aborts(tmp_path):
    # With the abort policy, an intruding grid must be reported as unsafe and the
    # simulation must flag that the real scan would abort -- yet still return the
    # zone geometry and unsafe points so the preview can show them.
    config_file, r_wall, z_min, z_max = _write_cylindrical_config_with_unsafe_points(
        tmp_path, 'abort'
    )
    sim = simulate_motion(config_file)

    assert sim.zone_kind == 'cylindrical'
    assert sim.unsafe_points, 'expected unsafe measurement points to be reported'
    assert sim.unsafe_point_policy == 'abort'
    assert sim.aborted is True
    # Every reported unsafe point really lies inside the protected interior.
    tol = 1e-6
    for point in sim.unsafe_points:
        assert point.r() < r_wall - tol
        assert z_min + tol < point.z() < z_max - tol


def test_simulate_motion_flags_unsafe_points_when_skipping(tmp_path):
    # With the skip policy the scan does not abort, but the unsafe points are
    # still reported so the preview can warn they will be skipped.
    config_file, _r_wall, _z_min, _z_max = _write_cylindrical_config_with_unsafe_points(
        tmp_path, 'skip'
    )
    sim = simulate_motion(config_file)

    assert sim.unsafe_points, 'expected unsafe measurement points to be reported'
    assert sim.unsafe_point_policy == 'skip'
    assert sim.aborted is False


def test_simulate_motion_starts_at_given_current_position(tmp_path):
    # The previewed path must begin at the supplied current arm position instead
    # of the origin (which usually sits inside the no-fly zone).
    config_file, _r_wall, _z_min, _z_max = _write_cylindrical_config(tmp_path)
    start = CylindricalPosition(150.0, 0.0, 200.0)
    sim = simulate_motion(config_file, start=start)

    path = sim.flatten()
    assert path, 'expected a recorded path'
    first_r, first_z = path[0]
    assert abs(first_r - start.r()) < 1e-6
    assert abs(first_z - start.z()) < 1e-6


def test_simulate_motion_never_enters_cylindrical_zone(tmp_path):
    config_file, r_wall, z_min, z_max = _write_cylindrical_config(tmp_path)
    sim = simulate_motion(config_file)

    tol = 1e-6
    for r, z in sim.flatten():
        inside = (r < r_wall - tol) and (z_min + tol < z < z_max - tol)
        assert not inside, f'path point (r={r:.3f}, z={z:.3f}) entered the no-fly zone'
