"""config.yml, the file that holds every secret the server has.

It is written readable by its owner only and replaced atomically, so a crash mid-write
never leaves an empty or world-readable config behind.
"""

import os
import platform

import yaml

from raven.defaultconfig import DefaultConfig


def default_config_dict() -> dict:
    """DefaultConfig's upper-case settings as a dict, the way the first run saves them."""
    conf = {}
    for option, value in DefaultConfig.__dict__.items():
        # Don't save a list of languages to the config, use the list in defaultconfig.py instead
        if option == "RAVEN_LANGUAGES" or not option.isupper():
            continue
        # Fix the sqlite DB path on Windows
        if (
            option == "SQLALCHEMY_DATABASE_URI"
            and platform.system() == "Windows"
            and value.startswith("sqlite")
        ):
            value = value.replace("////", "///").replace("\\", "/")
        conf[option] = value
    return conf


def write_config_yml(path: str, conf: dict) -> None:
    """Saves conf to path as YAML, mode 0600, replacing any existing file atomically."""
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml.safe_dump(conf))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_default_config(path: str) -> None:
    write_config_yml(path, default_config_dict())
