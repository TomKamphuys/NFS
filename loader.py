"""A simple plugin loader."""
import importlib
import configparser
from loguru import logger
import factory


class ModuleInterface:
    """
    Interface for modules to define a standard structure for functionality.

    This class provides a guideline for implementing modules that need to
    interface with a measurement points factory. Its purpose is to ensure
    that all modules can be registered consistently.

    :ivar attribute1: Description of attribute1.
    :type attribute1: type
    :ivar attribute2: Description of attribute2.
    :type attribute2: type
    """

    @staticmethod
    def register(my_factory) -> None:
        """
        Register the necessary items in the measurement points factory.
        """


def import_module(name: str) -> ModuleInterface:
    """
    Imports a module by its name dynamically and returns it as a usable module
    interface. This function utilizes Python's `importlib` to perform the import
    at runtime, providing flexibility in scenarios where the module name is
    determined programmatically.

    :param name: The name of the module to be imported.
    :type name: str
    :return: The imported module as a usable interface.
    :rtype: ModuleInterface
    """
    return importlib.import_module(name)  # type: ignore

def load_plugins(config_file):
    """
    Load and initialize plugins as defined in the configuration file.

    This function reads a configuration file and loads plugins defined in the
    'plugins' section. It utilizes a configuration parser to process inline
    comments and extract the relevant plugin definitions. The extracted plugins
    are then passed to an internal handler for loading.

    :param config_file: Path to the configuration file containing plugin definitions.
    :type config_file: str
    :return: None
    """
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)

    items = config_parser.items('plugins')
    _, plugins = zip(*items)

    # load the plugins
    _load_plugins(plugins)

def _load_plugins(plugins: list[str]) -> None:
    """
    Loads and registers a list of plugins using their module names.

    This function iterates through a given list of plugin module names. It imports
    each module dynamically, logs the loading of each plugin, and invokes the
    module's `register` method onto a provided factory. The factory is expected
    to be available in the current scope.

    :param plugins: A list of strings, each representing the module name of a
        plugin to be loaded and registered.
    :return: None
    """
    for plugin_file in plugins:
        logger.info(f'Loading plugin: {plugin_file}')
        plugin = import_module(plugin_file)
        plugin.register(factory)
