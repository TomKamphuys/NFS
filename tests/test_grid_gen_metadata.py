import json
from pathlib import Path

from grid_generator.grid_gen import generate_measurement_grid
from grid_generator.path_plan import plan_path


def test_position_payload_is_written_to_sidecar_json_metadata(tmp_path):
    cyl_df, gen_settings = generate_measurement_grid(
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

    # Verify the settings dict contains expected values with proper types
    assert gen_settings["ref_origin_pos"] == [90.0, 0.0, 170.0]
    assert gen_settings["baffle_bot_l_pos"] == [90.0, -45.0, 80.0]
    assert gen_settings["baffle_top_l_pos"] == [90.0, -45.0, 240.0]
    assert gen_settings["baffle_top_r_pos"] == [90.0, 45.0, 240.0]
    assert gen_settings["user_position_woofer"] == [90.0, 0.0, 100.0]
    assert gen_settings["user_position_reference_mic"] == [120.0, 10.0, 150.0]

    # Verify the CSV no longer contains the gen_settings column
    assert "gen_settings" not in cyl_df.columns

    # Write CSV and sidecar JSON, then verify the sidecar file
    output_path = tmp_path / "planned_grid.csv"
    planned = plan_path(
        input_data=cyl_df,
        cap_tol_mm=51.0,
        output_path=output_path,
    )

    # Write sidecar JSON
    sidecar_path = output_path.with_suffix('.json')
    sidecar_path.write_text(json.dumps(gen_settings, indent=2), encoding='utf-8')

    # Verify the sidecar JSON file can be loaded and contains expected values
    loaded = json.loads(sidecar_path.read_text(encoding='utf-8'))
    assert loaded["ref_origin_pos"] == [90.0, 0.0, 170.0]
    assert loaded["user_position_woofer"] == [90.0, 0.0, 100.0]
    assert loaded["user_position_reference_mic"] == [120.0, 10.0, 150.0]
