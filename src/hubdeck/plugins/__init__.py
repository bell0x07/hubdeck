from .audio import AudioPlugin
from .base import Plugin, PluginContext, ThreadedPlugin
from .discord import DiscordPlugin
from .keyboard import KeyboardPlugin
from .launch import LaunchPlugin
from .page import PagePlugin
from .system import SystemPlugin

REGISTRY: dict[str, type[Plugin]] = {
    p.name: p for p in (PagePlugin, KeyboardPlugin, LaunchPlugin, DiscordPlugin, AudioPlugin, SystemPlugin)
}

__all__ = ["REGISTRY", "Plugin", "PluginContext", "ThreadedPlugin"]
