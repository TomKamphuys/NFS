from __future__ import annotations

import configparser
import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loguru import logger


LEGACY_PROJECT_FILENAME = "project.json"
APP_SECTION = "app"
DEFAULT_PROJECT_NAME = "HALS_Project"
TEMP_PROJECT_DIR = Path(tempfile.gettempdir()) / "HALS_working_project"
GLOBAL_SWEEP_CONFIG_KEYS = {"sweep_level_dbfs"}
DEFAULT_CONFIG_FILENAME = "config_default.ini"

_project_dir = TEMP_PROJECT_DIR
_project_data: Dict[str, Any] = {}
_callbacks: list[Callable[[], None]] = []


def _truncate_float(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.trunc(value * factor) / factor


def sanitize_project_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(name).strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or DEFAULT_PROJECT_NAME


def get_project_dir() -> Path:
    return _project_dir


def is_temporary_project_dir() -> bool:
    try:
        return _project_dir.resolve() == TEMP_PROJECT_DIR.resolve()
    except OSError:
        return _project_dir == TEMP_PROJECT_DIR


def reset_temporary_project_dir() -> None:
    """Clear volatile startup project state from the app's working temp folder."""
    target = TEMP_PROJECT_DIR.expanduser().resolve()
    expected = (Path(tempfile.gettempdir()) / "HALS_working_project").resolve()
    if target != expected:
        raise RuntimeError(f"Refusing to reset unexpected temp project path: {target}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def get_project_json_path() -> Path:
    return _project_dir / f"{sanitize_project_name(get_project_name())}_project.json"


def get_project_data() -> Dict[str, Any]:
    return _project_data


def get_project_name() -> str:
    return str(_project_data.get("project_name") or DEFAULT_PROJECT_NAME)


def get_project_stem() -> str:
    return sanitize_project_name(get_project_name())


def get_grid_filename() -> str:
    return f"{get_project_stem()}_grid.csv"


def get_default_project_root(config_file: str = "config.ini") -> Path:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read(config_file)
    fallback = Path.home() / "Documents" / "HALS_Projects"
    value = parser.get(APP_SECTION, "default_project_dir", fallback=str(fallback))
    return Path(value).expanduser().resolve()


def set_default_project_root(path: str | Path, config_file: str = "config.ini") -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(config_file)
    if not parser.has_section(APP_SECTION):
        parser.add_section(APP_SECTION)
    parser.set(APP_SECTION, "default_project_dir", str(root))
    with open(config_file, "w", encoding="utf-8") as f:
        parser.write(f)
    return root


def on_project_changed(callback: Callable[[], None]) -> None:
    _callbacks.append(callback)


def _notify_project_changed() -> None:
    for callback in list(_callbacks):
        try:
            callback()
        except Exception as exc:
            message = str(exc).lower()
            if "deleted" in message or "slot" in message:
                try:
                    _callbacks.remove(callback)
                except ValueError:
                    pass
                logger.debug(f"Removed stale project change callback: {exc}")
                continue
            logger.warning(f"Project change callback failed: {exc}")


def set_project_dir(path: str | Path, config_file: str = "config.ini") -> Dict[str, Any]:
    global _project_dir, _project_data
    _project_dir = Path(path).expanduser().resolve()
    _project_dir.mkdir(parents=True, exist_ok=True)

    project_candidates = sorted(_project_dir.glob("*_project.json"))
    legacy_project_path = _project_dir / LEGACY_PROJECT_FILENAME
    project_path = project_candidates[0] if project_candidates else legacy_project_path
    loaded_existing = project_path.exists()
    if loaded_existing:
        try:
            _project_data = json.loads(project_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning(f"Could not parse {project_path}: {exc}")
            _project_data = {}
    else:
        default_name = (
            DEFAULT_PROJECT_NAME
            if _project_dir.name == "HALS_working_project"
            else sanitize_project_name(_project_dir.name)
        )
        _project_data = {"project_name": default_name}

    if not loaded_existing:
        sync_defaults_from_config(config_file, save=False)
    _notify_project_changed()
    return _project_data


def set_project_name(name: str) -> None:
    _project_data["project_name"] = str(name).strip() or DEFAULT_PROJECT_NAME
    _notify_project_changed()


def save_project() -> None:
    _project_dir.mkdir(parents=True, exist_ok=True)
    get_project_json_path().write_text(
        json.dumps(_project_data, indent=4),
        encoding="utf-8",
    )


def save_project_to(project_folder: str | Path, project_name: str, config_file: str = "config.ini") -> Path:
    """Persist the in-memory project snapshot into the selected measurement folder."""
    global _project_dir
    sync_from_config(config_file, save=False)
    set_project_name(project_name)
    target_dir = Path(project_folder).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    grid_vars = _project_data.get("grid_vars")
    if isinstance(grid_vars, dict):
        source_name = grid_vars.get("output_filename") or get_grid_filename()
        source = _project_dir / str(source_name)
        target_name = get_grid_filename()
        target = target_dir / target_name
        if source.exists() and source.resolve() != target.resolve():
            shutil.copy2(source, target)
        grid_vars["output_filename"] = target_name

    _project_dir = target_dir
    apply_to_config(config_file)
    save_project()
    _notify_project_changed()
    return target_dir


def ensure_output_dirs() -> None:
    for rel_path in (
        "measurement_set",
        "measurement_set/Distortion",
        "single_measurements",
        "single_measurements/Distortion",
        "logs",
    ):
        (_project_dir / rel_path).mkdir(parents=True, exist_ok=True)


def sync_from_config(config_file: str, save: bool = True) -> None:
    _sync_sweep_from_config(config_file, save=save)


def sync_defaults_from_config(config_file: str, save: bool = True) -> None:
    _sync_sweep_from_config(get_default_config_path(config_file), save=save)


def get_default_config_path(config_file: str | Path = "config.ini") -> Path:
    config_path = Path(config_file)
    candidates = [
        config_path.with_name(DEFAULT_CONFIG_FILENAME),
        Path(DEFAULT_CONFIG_FILENAME),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return config_path


def _sync_sweep_from_config(config_file: str | Path, save: bool = True) -> None:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read(config_file)

    for section in ("sweep",):
        if parser.has_section(section):
            settings = dict(parser.items(section))
            if section == "sweep":
                settings = _project_sweep_settings(settings)
            _project_data[f"{section}_settings"] = settings

    if save:
        save_project()


def apply_to_config(config_file: str) -> bool:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(config_file)

    changed = False
    for project_key, section in (("sweep_settings", "sweep"),):
        settings = _project_data.get(project_key)
        if not isinstance(settings, dict):
            continue
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in settings.items():
            if section == "audio" and key == "mode":
                continue
            parser.set(section, key, str(value))
            changed = True

    grid_vars = _project_data.get("grid_vars")
    if isinstance(grid_vars, dict) and parser.has_section("motion_manager"):
        filename = grid_vars.get("output_filename")
        if filename:
            filename = str((_project_dir / str(filename)).resolve())
            parser.set("motion_manager", "measurement_points_type", "FileMeasurementPoints")
            parser.set("motion_manager", "filename", str(filename))
            changed = True

    if changed:
        with open(config_file, "w", encoding="utf-8") as f:
            parser.write(f)
    return changed


def update_grid_vars(grid_vars: Dict[str, Any]) -> None:
    current = _project_data.get("grid_vars")
    if not isinstance(current, dict):
        current = {}
    current.update(grid_vars)
    _project_data["grid_vars"] = current


def build_spl_calibration(
    spl_db: Any,
    reference_input_rms_dbfs: Any,
    spl_offset_db: Any = None,
    spl_meter_weighting: Any = None,
) -> Optional[Dict[str, Any]]:
    if (
        spl_db is None
        and reference_input_rms_dbfs is None
        and spl_offset_db is None
        and spl_meter_weighting is None
    ):
        return None
    spl_db_float = None if spl_db is None else float(spl_db)
    reference_float = (
        None
        if reference_input_rms_dbfs is None
        else float(reference_input_rms_dbfs)
    )
    offset_float = (
        float(spl_offset_db)
        if spl_offset_db is not None
        else (
            None
            if spl_db_float is None or reference_float is None
            else spl_db_float - reference_float
        )
    )
    if offset_float is not None:
        offset_float = _truncate_float(offset_float, 2)
    calibration = {
        "frd_db_offset": offset_float,
        "spl_db": spl_db_float,
        "reference_input_rms_dbfs": reference_float,
    }
    if spl_meter_weighting is not None:
        calibration["spl_meter_weighting"] = str(spl_meter_weighting)
    return calibration


def update_audio_setup(
    audio_settings: Dict[str, Any],
    sweep_settings: Dict[str, Any],
    spl_calibration: Optional[Dict[str, Any]] = None,
) -> None:
    # Audio hardware is intentionally global via config.ini. Only sweep/project
    # measurement settings are captured in a saved project snapshot.
    _project_data["sweep_settings"] = _project_sweep_settings(sweep_settings)
    if spl_calibration is not None:
        update_spl_calibration(spl_calibration)


def _project_sweep_settings(sweep_settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in sweep_settings.items()
        if key not in GLOBAL_SWEEP_CONFIG_KEYS
    }


def update_spl_calibration(spl_calibration: Dict[str, Any]) -> None:
    current = _project_data.get("stage5_vars")
    if not isinstance(current, dict):
        current = {}
    current.update(spl_calibration)
    _project_data["stage5_vars"] = current
    _project_data.pop("spl_calibration", None)
