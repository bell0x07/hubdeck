"""Read config.yaml (+ secrets.yaml), validate it and watch it for changes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .. import paths
from .schema import TEMPLATE_RE, Config


class ConfigError(Exception):
    pass


class Secret:
    def __init__(self, name: str):
        self.name = name


class _Loader(yaml.SafeLoader):
    pass


# YAML 1.2 booleans: only true/false. Otherwise `label: ON` or `label: off` turns into a bool.
_Loader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                              list("tTfF"))
_Loader.add_constructor("!secret", lambda loader, node: Secret(loader.construct_scalar(node)))


def _read_yaml(path: Path, loader=_Loader) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (create one with: hubdeck init)")
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML\n  {e}")


def _resolve_secrets(data: Any, secrets: dict, missing: list[str]) -> Any:
    if isinstance(data, Secret):
        if data.name in secrets:
            return secrets[data.name]
        env = f"HUBDECK_{data.name.upper()}"
        if env in os.environ:
            return os.environ[env]
        missing.append(data.name)
        return None
    if isinstance(data, dict):
        return {k: _resolve_secrets(v, secrets, missing) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_secrets(v, secrets, missing) for v in data]
    return data


def format_validation_error(e: ValidationError) -> str:
    lines = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(top level)"
        msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"  {loc}: {msg}")
    return "\n".join(lines)


def check_references(cfg: Config) -> list[str]:
    """Problems pydantic can't see: unknown plugins/actions/states, page links, plugin settings."""
    from ..plugins import REGISTRY

    problems = []
    for name in sorted(cfg.used_plugins()):
        if name not in REGISTRY:
            problems.append(f"unknown plugin {name!r} (available: {', '.join(sorted(REGISTRY))})")
    for name, settings in cfg.plugins.items():
        if name in REGISTRY:
            problems += REGISTRY[name].check_config(settings)
    for page, num, kd in cfg.iter_keys():
        where = f"pages.{page}.keys.{num}"
        for a in kd.all_actions():
            plugin = REGISTRY.get(a.plugin)
            if plugin is None:
                continue
            if a.name not in plugin.actions:
                problems.append(f"{where}: {a.plugin} has no action {a.name!r} "
                                f"(available: {', '.join(plugin.actions) or 'none'})")
                continue
            if err := plugin.check_action(a.name, a.arg):
                problems.append(f"{where}: {err}")
            if a.plugin == "page" and a.name == "goto" and isinstance(a.arg, str) and a.arg not in cfg.pages:
                problems.append(f"{where}: no page named {a.arg!r}")
        refs = []
        if kd.state:
            refs.append(kd.state)
        for f in kd.faces():
            for s in (f.text, f.label):
                refs += [f"{p}.{n}" for p, n, _ in TEMPLATE_RE.findall(s or "")]
        for ref in refs:
            p, n = ref.split(".", 1)
            if p in REGISTRY and n not in REGISTRY[p].states:
                problems.append(f"{where}: {p} has no state {n!r} "
                                f"(available: {', '.join(REGISTRY[p].states) or 'none'})")
        if (kd.face_on or kd.face_unknown) and not kd.state:
            problems.append(f"{where}: face_on/face_unknown need a 'state' to switch on")
    return problems


def load_config(path: Path, secrets: Path | None = None) -> Config:
    path = Path(path)
    data = _read_yaml(path)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    secrets = Path(secrets) if secrets else paths.secrets_path(path)
    secret_values = {}
    if secrets.exists():
        secret_values = _read_yaml(secrets, yaml.SafeLoader) or {}
        if not isinstance(secret_values, dict):
            raise ConfigError(f"{secrets}: expected 'name: value' lines")
    missing: list[str] = []
    data = _resolve_secrets(data, secret_values, missing)
    if missing:
        raise ConfigError(f"{path}: secret(s) {', '.join(missing)} not found in {secrets} "
                          f"or HUBDECK_<NAME> environment variables")
    try:
        cfg = Config.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"{path}: invalid config\n{format_validation_error(e)}")
    problems = check_references(cfg)
    if problems:
        raise ConfigError(f"{path}: invalid config\n" + "\n".join(f"  {p}" for p in problems))
    return cfg


class ConfigSource:
    """A config file plus its secrets file, with change detection by mtime."""

    def __init__(self, path: Path | None = None, secrets: Path | None = None):
        self.path = Path(path) if path else paths.config_path()
        self.secrets = Path(secrets) if secrets else paths.secrets_path(self.path)
        self._stamp = self._mtimes()

    def _mtimes(self):
        out = []
        for p in (self.path, self.secrets):
            try:
                out.append(p.stat().st_mtime_ns)
            except OSError:
                out.append(None)
        return tuple(out)

    def load(self) -> Config:
        self._stamp = self._mtimes()
        return load_config(self.path, self.secrets)

    def changed(self) -> bool:
        return self._mtimes() != self._stamp
