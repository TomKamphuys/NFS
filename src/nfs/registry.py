from typing import Any, Callable, Dict, TypeVar, Type

T = TypeVar("T")


class Registry:
    """
    A unified registry for various component types (e.g., MeasurementPoints, Audio, etc.).
    """

    def __init__(self, name: str):
        self.name = name
        self._registry: Dict[str, Any] = {}

    def register(self, name: str, component: Any) -> None:
        """Register a component with a name."""
        self._registry[name] = component

    def get(self, name: str) -> Any:
        """Get a registered component by its name."""
        if name not in self._registry:
            raise ValueError(f"Unknown {self.name} type: {name!r}")
        return self._registry[name]

    def list(self):
        """List all registered components."""
        return list(self._registry.keys())


# Global registries for different component categories
measurement_points = Registry("MeasurementPoints")
audio = Registry("Audio")
motion_managers = Registry("MotionManager")
