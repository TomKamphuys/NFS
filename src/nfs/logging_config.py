import configparser
import sys
from loguru import logger

_initialized = False


def setup_logging(config_file: str = "config.ini"):
    """
    Sets up logging for the application using loguru.
    Configures loggers based on settings in the provided configuration file.
    
    Expected config structure:
    [logging]
    level = INFO
    file = scanner.log
    rotation = 10 MB
    retention = 1 week
    """
    global _initialized
    if _initialized:
        return

    # Remove default handler
    logger.remove()

    # Default values
    level = "INFO"
    log_file = "scanner.log"
    rotation = "10 MB"
    retention = "1 week"

    config = configparser.ConfigParser(inline_comment_prefixes="#")
    try:
        if config.read(config_file):
            if config.has_section("logging"):
                level = config.get("logging", "level", fallback=level)
                log_file = config.get("logging", "file", fallback=log_file)
                rotation = config.get("logging", "rotation", fallback=rotation)
                retention = config.get("logging", "retention", fallback=retention)
    except Exception as e:
        # If we can't read config, we'll use defaults and log the error once stderr logger is up
        print(f"Warning: Could not read logging config from {config_file}: {e}", file=sys.stderr)

    # Add stderr handler
    logger.add(sys.stderr, level=level,
               format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")

    # Add file handler
    if log_file:
        logger.add(
            log_file,
            level=level,
            rotation=rotation,
            retention=retention,
            compression="zip",
            mode="w",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
        )

    logger.info(f"Logging initialized at level {level}")
    if log_file:
        logger.info(f"Logging to file: {log_file}")

    _initialized = True


def get_logger():
    """Returns the loguru logger instance."""
    return logger
