"""Factory for creating a MeasurementPoints."""

from typing import Any, Callable

from .measurement_points import MeasurementPoints

measurement_points_creation_funcs: dict[str, Callable[..., MeasurementPoints]] = {}


def register(measurement_points_type: str, creator_fn: Callable[..., MeasurementPoints]) -> None:
    """Register a new MeasurementPoints type."""
    measurement_points_creation_funcs[measurement_points_type] = creator_fn


def unregister(measurement_points_type: str) -> None:
    """Unregister a MeasurementPoints type."""
    measurement_points_creation_funcs.pop(measurement_points_type, None)


def create(arguments: dict[str, Any]) -> MeasurementPoints:
    """
    Create an instance of `MeasurementPoints` based on the provided arguments.

    This function selects an appropriate `creator_func` from the
    `measurement_points_creation_funcs` dictionary using the "type" key in the
    arguments. It then uses this function to create and return the desired
    `MeasurementPoints` instance. If the specified type is not found in the
    dictionary, an exception is raised. The "type" key in the `arguments` dictionary
    is extracted and is not passed to the creator function.

    :param arguments:
        A dictionary where keys are string identifiers and their values include
        the required configuration for the measurement points object. The
        "type" key specifies the type of measurement points to create, used to
        look up the appropriate creation function.

    :return:
        An instance of `MeasurementPoints`, created using the specified type and
        configuration in the `arguments`.

    :raises ValueError:
        If the "type" value in the `arguments` dictionary is not recognized in
        the `measurement_points_creation_funcs` mapping.
    """
    args_copy = arguments.copy()
    measurement_points_type = args_copy.pop("type")
    try:
        creator_func = measurement_points_creation_funcs[measurement_points_type]
    except KeyError:
        raise ValueError(f"unknown MeasurementPoints type {measurement_points_type!r}") from None
    return creator_func(**args_copy)
