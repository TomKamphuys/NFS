"""Factory for creating a MeasurementPoints."""

from typing import Any, Callable
from measurement_points import MeasurementPoints

measurement_points_creation_funcs: dict[str, Callable[..., MeasurementPoints]] = {}


def register(character_type: str, creator_fn: Callable[..., MeasurementPoints]) -> None:
    """Register a new MeasurementPoints type."""
    measurement_points_creation_funcs[character_type] = creator_fn


def unregister(character_type: str) -> None:
    """Unregister a MeasurementPoints type."""
    measurement_points_creation_funcs.pop(character_type, None)


def create(arguments: dict[str, Any]) -> MeasurementPoints:
    """Create a game character of a specific type, given dict data."""
    args_copy = arguments.copy()
    measurement_points_type = args_copy.pop("type")
    try:
        creator_func = measurement_points_creation_funcs[measurement_points_type]
    except KeyError:
        raise ValueError(f"unknown character type {measurement_points_type!r}") from None
    return creator_func(**args_copy)
