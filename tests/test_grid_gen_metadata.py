from grid_generator.grid_gen import generate_measurement_grid
from grid_generator.path_plan import plan_path


def test_position_payload_is_written_to_planned_csv_metadata(tmp_path):
    grid = generate_measurement_grid(
        cyl_radius_mm=200.0,
        cyl_height_mm=500.0,
        num_points=40,
        bottom_cutoff_mm=30.0,
        tweeter_pos=(90.0, 0.0, 200.0),
        additional_positions=[
            ("Woofer", (90.0, 0.0, 100.0)),
            ("Reference Mic", (120.0, 10.0, 150.0)),
        ],
        ref_origin_pos=(90.0, 0.0, 170.0),
        baffle_bot_l_pos=(90.0, -45.0, 80.0),
        baffle_top_l_pos=(90.0, -45.0, 240.0),
        baffle_top_r_pos=(90.0, 45.0, 240.0),
        top_crit_pos=(120.0, 0.0, 240.0),
        bot_crit_pos=(30.0, 0.0, -30.0),
    )
    output_path = tmp_path / "planned_grid.csv"
    planned = plan_path(
        input_data=grid,
        cap_tol_mm=51.0,
        output_path=output_path,
    )

    metadata = set(planned["gen_settings"])

    assert "ref_origin_pos=(90.0, 0.0, 170.0)" in metadata
    assert "baffle_bot_l_pos=(90.0, -45.0, 80.0)" in metadata
    assert "baffle_top_l_pos=(90.0, -45.0, 240.0)" in metadata
    assert "baffle_top_r_pos=(90.0, 45.0, 240.0)" in metadata
    assert "user_position_woofer=(90.0, 0.0, 100.0)" in metadata
    assert "user_position_reference_mic=(120.0, 10.0, 150.0)" in metadata
    assert "user_position_woofer=(90.0, 0.0, 100.0)" in output_path.read_text(encoding="utf-8")
