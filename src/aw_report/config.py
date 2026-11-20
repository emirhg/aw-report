"""
User configuration loading and settings resolution.

Provides TOML config file support with XDG Base Directory specification.
Follows XDG standard: $XDG_CONFIG_HOME/tw-report/config.toml
Fallback: ~/.config/tw-report/config.toml

Sections:
- ``[buckets]``: role-to-bucket overrides, validated in core/buckets.py.
- ``[activitywatch]``: server address (host, port) and the timezone used for period
  boundaries. Host and port fall back to ActivityWatch's own aw-client.toml, then to
  127.0.0.1:5600.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aw_report.exceptions import ConfigParsingError

DEFAULT_AW_HOST = "127.0.0.1"
DEFAULT_AW_PORT = 5600
ACTIVITYWATCH_KEYS = frozenset({"host", "port", "timezone"})


@dataclass(frozen=True)
class ResolvedSettings:
    """Merged configuration from CLI args, config file, and defaults."""

    detail_level: int
    day_start_hour: int = 0
    exclude_projects: list | None = None
    exclude_tasks: list | None = None
    exclude_apps: list | None = None
    terminal_width: int | None = None
    categories_file: str | None = None
    timezone: str | None = None
    aw_host: str = DEFAULT_AW_HOST
    aw_port: int = DEFAULT_AW_PORT


def _xdg_config_home() -> Path:
    """Return $XDG_CONFIG_HOME when it is absolute, otherwise ~/.config.

    Raises:
        ValueError: If the home directory cannot be determined
    """
    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    if xdg_config_home.is_absolute():
        return xdg_config_home
    home = Path.home()
    if not home.exists():
        raise ValueError("Could not determine home directory")
    return home / ".config"


def get_config_path() -> Path:
    """Resolve config file path following XDG Base Directory specification.

    Returns:
        Path to config file: $XDG_CONFIG_HOME/tw-report/config.toml
        or ~/.config/tw-report/config.toml if XDG_CONFIG_HOME not set

    Raises:
        ValueError: If home directory cannot be determined
    """
    return _xdg_config_home() / "tw-report" / "config.toml"


def get_aw_client_config_path() -> Path:
    """Path of ActivityWatch's own client config, used as the server-address fallback."""
    return _xdg_config_home() / "activitywatch" / "aw-client" / "aw-client.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file: {} if missing, ConfigParsingError if malformed."""
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        raise ConfigParsingError(f"Failed to parse config file {path}: {e}") from e


def load_user_config(path: Path | None = None) -> dict[str, Any]:
    """Load user configuration from TOML file.

    Args:
        path: Optional path to config file. If not provided, uses get_config_path()

    Returns:
        Dictionary of configuration options (empty dict if file doesn't exist)

    Raises:
        ConfigParsingError: If TOML file is malformed
    """
    if path is None:
        path = get_config_path()
    return _read_toml(path)


def load_aw_server_address(
    user_config: dict[str, Any] | None = None,
    aw_client_config_path: Path | None = None,
) -> tuple[str, int]:
    """Resolve the ActivityWatch server host and port.

    Precedence: [activitywatch] host/port in config.toml, then [server] hostname/port in
    ActivityWatch's aw-client.toml, then 127.0.0.1:5600.

    Raises:
        ConfigParsingError: On an unknown key in [activitywatch] or a non-integer port.
    """
    if user_config is None:
        user_config = load_user_config()
    if aw_client_config_path is None:
        aw_client_config_path = get_aw_client_config_path()

    section = validate_activitywatch_section(user_config.get("activitywatch", {}))
    server = _read_toml(aw_client_config_path).get("server", {})

    host = section.get("host") or server.get("hostname") or DEFAULT_AW_HOST
    port = section.get("port") or server.get("port") or DEFAULT_AW_PORT
    try:
        port = int(port)
    except (TypeError, ValueError) as e:
        raise ConfigParsingError(f"Invalid ActivityWatch port: {port!r}") from e
    return str(host), port


def validate_activitywatch_section(section: Any) -> dict[str, Any]:
    """Return the [activitywatch] table, rejecting anything but host, port and timezone."""
    if not isinstance(section, dict):
        raise ConfigParsingError("[activitywatch] must be a table")
    unknown = set(section) - ACTIVITYWATCH_KEYS
    if unknown:
        raise ConfigParsingError(
            f"Unknown key(s) in [activitywatch]: {', '.join(sorted(unknown))} "
            f"(accepted: host, port, timezone)"
        )
    return section


def resolve_settings(
    cli_args: dict[str, Any] | None = None,
    user_config: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> ResolvedSettings:
    """Resolve final settings from CLI args, config file, and defaults.

    Precedence: CLI args > config file > defaults

    Args:
        cli_args: Command-line arguments (takes highest precedence)
        user_config: User config file settings
        defaults: Built-in defaults

    Returns:
        ResolvedSettings object with merged configuration
    """
    if cli_args is None:
        cli_args = {}
    if user_config is None:
        user_config = {}
    if defaults is None:
        defaults = {
            "detail_level": 4,
            "day_start_hour": 0,
            "exclude_projects": None,
            "exclude_tasks": None,
            "exclude_apps": None,
            "terminal_width": None,
            "categories_file": None,
        }

    # Merge settings with CLI args taking precedence
    settings = {**defaults, **user_config}
    for key, value in cli_args.items():
        if value is not None:
            settings[key] = value

    activitywatch = validate_activitywatch_section(user_config.get("activitywatch", {}))
    host, port = load_aw_server_address(user_config)

    return ResolvedSettings(
        detail_level=settings.get("detail_level", defaults.get("detail_level", 4)),
        day_start_hour=settings.get("day_start_hour", defaults.get("day_start_hour", 0)),
        exclude_projects=settings.get("exclude_projects"),
        exclude_tasks=settings.get("exclude_tasks"),
        exclude_apps=settings.get("exclude_apps"),
        terminal_width=settings.get("terminal_width"),
        categories_file=settings.get("categories_file"),
        timezone=activitywatch.get("timezone"),
        aw_host=host,
        aw_port=port,
    )
