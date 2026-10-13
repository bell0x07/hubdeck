"""Where hubdeck keeps its files. Override the folder with HUBDECK_HOME."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "hubdeck"
EXAMPLE_CONFIG = Path(__file__).resolve().parent / "assets" / "example-config.yaml"
EXAMPLE_SECRETS = Path(__file__).resolve().parent / "assets" / "example-secrets.yaml"
PLACEHOLDER_PREFIX = "paste-your-"  # values in example-secrets.yaml that still need replacing


def app_dir() -> Path:
    if os.environ.get("HUBDECK_HOME"):
        return Path(os.environ["HUBDECK_HOME"])
    from .platforms import current
    return current().app_data_dir() / APP_NAME


def config_path() -> Path:
    return app_dir() / "config.yaml"


def secrets_path(config: Path | None = None) -> Path:
    """secrets.yaml lives next to whichever config file is in use."""
    return (config.parent if config else app_dir()) / "secrets.yaml"


def log_path() -> Path:
    return app_dir() / "hubdeck.log"


def discord_token_path() -> Path:
    return app_dir() / "discord_token.json"


def spotify_token_path() -> Path:
    return app_dir() / "spotify_token.json"
