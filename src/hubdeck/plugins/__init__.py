from .audio import AudioPlugin
from .base import Plugin, PluginContext, ThreadedPlugin
from .discord import DiscordPlugin
from .keyboard import KeyboardPlugin
from .launch import LaunchPlugin
from .page import PagePlugin
from .shortcuts import ShortcutsPlugin
from .system import SystemPlugin
from .teams import TeamsPlugin
from .vars import VarsPlugin

REGISTRY: dict[str, type[Plugin]] = {
    p.name: p for p in (PagePlugin, KeyboardPlugin, LaunchPlugin, DiscordPlugin, TeamsPlugin, AudioPlugin,
                        SystemPlugin, VarsPlugin, ShortcutsPlugin)
}

__all__ = ["REGISTRY", "Plugin", "PluginContext", "ThreadedPlugin"]
