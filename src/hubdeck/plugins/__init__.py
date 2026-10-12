from .audio import AudioPlugin
from .base import Plugin, PluginContext, ThreadedPlugin
from .discord import DiscordPlugin
from .keyboard import KeyboardPlugin
from .launch import LaunchPlugin
from .page import PagePlugin
from .python import PythonPlugin
from .shortcuts import ShortcutsPlugin
from .system import SystemPlugin
from .vars import VarsPlugin

REGISTRY: dict[str, type[Plugin]] = {
    p.name: p for p in (PagePlugin, KeyboardPlugin, LaunchPlugin, PythonPlugin, DiscordPlugin, AudioPlugin,
                        SystemPlugin, VarsPlugin, ShortcutsPlugin)
}

__all__ = ["REGISTRY", "Plugin", "PluginContext", "ThreadedPlugin"]
