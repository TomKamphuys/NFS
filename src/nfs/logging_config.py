import os
import configparser
import sys
import subprocess
import platform
from loguru import logger

_initialized = False


def get_git_info(repo_path: str = None):
    """Retrieves git versioning information."""
    try:
        # Get branch name
        branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], 
                                         stderr=subprocess.DEVNULL, cwd=repo_path).decode().strip()
        # Get commit hash
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], 
                                         stderr=subprocess.DEVNULL, cwd=repo_path).decode().strip()
        # Check for dirty status
        status = subprocess.check_output(['git', 'status', '--porcelain'], 
                                         stderr=subprocess.DEVNULL, cwd=repo_path).decode().strip()
        is_dirty = "DIRTY" if status else "CLEAN"
        
        # Get human-readable version (tags)
        try:
            version = subprocess.check_output(['git', 'describe', '--tags', '--always'], 
                                              stderr=subprocess.DEVNULL, cwd=repo_path).decode().strip()
        except:
            version = commit[:7]

        return {
            "version": version,
            "branch": branch,
            "commit": commit,
            "status": is_dirty
        }
    except Exception:
        return None


def log_version_info(project_name: str = "NFS Project", repo_path: str = None, log_env: bool = True):
    """Logs the software version and optionally environment details."""
    git = get_git_info(repo_path)
    
    if log_env:
        logger.info("=" * 50)
        logger.info(f"{project_name} Initialization")
    else:
        logger.info(f"--- {project_name} version ---")
        
    if git:
        logger.info(f"Version: {git['version']} ({git['branch']})")
        logger.info(f"Commit:  {git['commit']}")
        logger.info(f"Status:  {git['status']}")
    else:
        logger.info("Version: Unknown (Git not found or not a repository)")
    
    if log_env:
        logger.info(f"OS:      {platform.system()} {platform.release()} ({platform.machine()})")
        logger.info(f"Python:  {platform.python_version()}")
        logger.info("=" * 50)


def setup_logging(config_file: str = "config.ini", project_name: str = "NFS Project"):
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

    log_version_info(project_name)
    
    # Also log NFS version if this is another project
    if project_name != "NFS Project":
        nfs_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_version_info("NFS Project", repo_path=nfs_path, log_env=False)
    
    _initialized = True


def get_logger():
    """Returns the loguru logger instance."""
    return logger
