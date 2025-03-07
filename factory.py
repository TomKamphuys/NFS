"""Factory for creating a MeasurementPoints."""

from typing import Any, Callable
from measurement_points import MeasurementPoints

measurement_points_creation_funcs: dict[str, Callable[..., MeasurementPoints]] = {}


def register(measurement_points_type: str, creator_fn: Callable[..., MeasurementPoints]) -> None:
    """Register a new MeasurementPoints type."""
    measurement_points_creation_funcs[measurement_points_type] = creator_fn


def unregister(measurement_points_type: str) -> None:
    """Unregister a MeasurementPoints type."""
    measurement_points_creation_funcs.pop(measurement_points_type, None)


def create(arguments: dict[str, Any]) -> MeasurementPoints:
    """
    Creates an instance of MeasurementPoints based on the provided arguments.

    This function dynamically determines which specific factory function to call by
    examining the "type" key in the given dictionary. It retrieves the appropriate
    factory function from a predefined mapping and then invokes that function with
    the remaining arguments. If an unknown type is provided, an exception is raised.

    :param arguments: A dictionary containing the type of measurement points under
        the "type" key, along with the additional arguments required for creating
        the measurement points instance.
    :type arguments: dict[str, Any]
    :return: An instance of MeasurementPoints created using the corresponding
        factory function.
    :rtype: MeasurementPoints
    :raises ValueError: If the "type" key is not present in the predefined mapping
        `measurement_points_creation_funcs`.
    """
    args_copy = arguments.copy()
    measurement_points_type = args_copy.pop("type")
    try:
        creator_func = measurement_points_creation_funcs[measurement_points_type]
    except KeyError:
        raise ValueError(f"unknown MeasurementPoints type {measurement_points_type!r}") from None
    return creator_func(**args_copy)
