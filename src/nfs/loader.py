"""A simple plugin loader."""
import configparser
import importlib
import sys
from typing import Optional

if sys.version_info >= (3, 10):
    from importlib.metadata import entry_points
else:
    from importlib_metadata import entry_points

from loguru import logger

from . import factory, registry

class ModuleInterface:
    """
    Interface definition for modules that can be registered with a factory.
    """
    @staticmethod
    def register(my_factory) -> None:
        """
        Register the necessary items in the measurement points factory.
        """

def import_module(name: str) -> ModuleInterface:
    """
    Imports a module dynamically by its name and returns the module object.
    """
    return importlib.import_module(name)  # type: ignore

def load_plugins(config_file: Optional[str] = None, plugins_section: Optional[str] = None) -> None:
    """
    Load and initialize plugins from both entry points and configuration file.
    """
    # 1. Load from entry points (Standard Pythonic way)
    _load_entry_points()

    # 2. Load from configuration file (Legacy/Manual way)
    if config_file and plugins_section:
        _load_config_plugins(config_file, plugins_section)

def _load_entry_points() -> None:
    """
    Discovers and loads plugins registered via entry points.
    """
    groups = [
        "nfs.measurement_points",
        "nfs.audio",
        "nfs.motion_managers"
    ]
    
    eps = entry_points()
    
    for group in groups:
        # entry_points().select(group=group) is available in Python 3.10+
        if hasattr(eps, 'select'):
            group_eps = eps.select(group=group)
        else:
            group_eps = eps.get(group, [])

        for entry_point in group_eps:
            logger.info(f"Loading plugin from entry point: {entry_point.name} (group: {group})")
            try:
                plugin_component = entry_point.load()
                # Check if it has a register function, or if it's a component itself
                if hasattr(plugin_component, 'register'):
                    # Call register with the appropriate registry
                    if group == "nfs.measurement_points":
                        plugin_component.register(factory) # keep backward compatibility with factory
                    elif group == "nfs.audio":
                        registry.audio.register(entry_point.name, plugin_component)
                    elif group == "nfs.motion_managers":
                        registry.motion_managers.register(entry_point.name, plugin_component)
                else:
                    # Register the component directly if no register() is provided
                    if group == "nfs.measurement_points":
                        registry.measurement_points.register(entry_point.name, plugin_component)
                    elif group == "nfs.audio":
                        registry.audio.register(entry_point.name, plugin_component)
                    elif group == "nfs.motion_managers":
                        registry.motion_managers.register(entry_point.name, plugin_component)
            except Exception as e:
                logger.error(f"Failed to load plugin {entry_point.name}: {e}")

def _load_config_plugins(config_file: str, plugins_section: str) -> None:
    """
    Load plugins listed in the specified configuration section.
    """
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)

    if not config_parser.has_section(plugins_section):
        return

    items = config_parser.items(plugins_section)
    if not items:
        return
        
    _, plugins = zip(*items)
    for plugin_file in plugins:
        logger.info(f'Loading legacy plugin: {plugin_file}')
        try:
            plugin = import_module(plugin_file)
            plugin.register(factory)
        except Exception as e:
            logger.error(f"Failed to load legacy plugin {plugin_file}: {e}")
