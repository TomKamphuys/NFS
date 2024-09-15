"""A simple plugin loader."""
import importlib
import configparser
from loguru import logger
import factory


class ModuleInterface:
    """
    Represents a plugin interface. A plugin has a single register function.
    """

    @staticmethod
    def register(my_factory) -> None:
        """
        Register the necessary items in the measurement points factory.
        """


def import_module(name: str) -> ModuleInterface:
    """Imports a module given a name."""
    return importlib.import_module(name)  # type: ignore

def load_plugins(config_file):
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)

    items = config_parser.items('plugins')
    _, plugins = zip(*items)

    # load the plugins
    _load_plugins(plugins)

def _load_plugins(plugins: list[str]) -> None:
    """Loads the plugins defined in the plugins list."""
    for plugin_file in plugins:
        logger.info(f'Loading plugin: {plugin_file}')
        plugin = import_module(plugin_file)
        plugin.register(factory)
