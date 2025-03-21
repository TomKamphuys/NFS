"""A simple plugin loader."""
import configparser
import importlib

from loguru import logger

import factory


class ModuleInterface:
    """
    Interface definition for modules that can be registered with a factory.

    This class provides a static method to register modules with a factory,
    enabling the integration of necessary measurement points or other
    functionality. Intended to be used as a base definition for module
    implementations.

    """

    @staticmethod
    def register(my_factory) -> None:
        """
        Register the necessary items in the measurement points factory.
        """


def import_module(name: str) -> ModuleInterface:
    """
    Imports a module dynamically by its name and returns the module object. This function utilizes the
    `importlib.import_module` method and expects the name of the target module as a string. If the import
    fails, an exception may be raised, typically `ModuleNotFoundError` or other related errors raised
    by the `importlib`.

    :param name: The name of the module to be imported. It should be a fully qualified name if the
        module is located in a package, e.g., 'package.module'.
    :type name: str

    :return: The imported module object.
    :rtype: ModuleInterface
    """
    return importlib.import_module(name)  # type: ignore

def load_plugins(config_file):
    """
    Load and initialize plugins listed in the specified configuration file.

    This function reads the provided configuration file to access the list of
    plugins under the section "plugins". Once the plugins are extracted,
    it initializes and loads them by delegating the operation to an internal
    method.

    :param config_file: Path to the configuration file to parse for plugin
        information.
    :type config_file: str
    :return: None
    :rtype: NoneType
    """
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)

    items = config_parser.items('plugins')
    _, plugins = zip(*items)

    # load the plugins
    _load_plugins(plugins)

def _load_plugins(plugins: list[str]) -> None:
    """
    Loads and registers a list of plugin modules using their file names.

    This function iterates over a list of plugin file names, dynamically imports
    each module, and registers them using a predefined factory. It ensures that
    each plugin is properly loaded and registered for further use.

    :param plugins: List of plugin file names to be imported and registered.
    :type plugins: list[str]
    :return: None
    """
    for plugin_file in plugins:
        logger.info(f'Loading plugin: {plugin_file}')
        plugin = import_module(plugin_file)
        plugin.register(factory)
