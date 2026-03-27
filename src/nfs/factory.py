"""Factory for creating a MeasurementPoints."""

from typing import Any, Callable
from . import registry
from .measurement_points import MeasurementPoints

def register(measurement_points_type: str, creator_fn: Callable[..., MeasurementPoints]) -> None:
    """Register a new MeasurementPoints type."""
    registry.measurement_points.register(measurement_points_type, creator_fn)

def unregister(measurement_points_type: str) -> None:
    """Unregister a MeasurementPoints type."""
    # pylint: disable=protected-access
    registry.measurement_points._registry.pop(measurement_points_type, None)

def create(arguments: dict[str, Any]) -> MeasurementPoints:
    """
    Create an instance of `MeasurementPoints` based on the provided arguments.
    """
    args_copy = arguments.copy()
    measurement_points_type = args_copy.pop("type")
    creator_func = registry.measurement_points.get(measurement_points_type)
    return creator_func(**args_copy)
