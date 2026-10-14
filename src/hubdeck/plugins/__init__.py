from .audio import AudioPlugin
from .base import Plugin, PluginContext, ThreadedPlugin
from .discord import DiscordPlugin
from .ha import HomeAssistantPlugin
from .hwinfo import HwinfoPlugin
from .keyboard import KeyboardPlugin
from .launch import LaunchPlugin
from .media import MediaPlugin
from .page import PagePlugin
from .python import PythonPlugin
from .shortcuts import ShortcutsPlugin
from .spotify import SpotifyPlugin
from .system import SystemPlugin
from .vars import VarsPlugin

REGISTRY: dict[str, type[Plugin]] = {
    p.name: p for p in (PagePlugin, KeyboardPlugin, LaunchPlugin, PythonPlugin, DiscordPlugin, AudioPlugin,
                        SystemPlugin, MediaPlugin, VarsPlugin, ShortcutsPlugin, SpotifyPlugin,
                        HomeAssistantPlugin, HwinfoPlugin)
}

__all__ = ["REGISTRY", "Plugin", "PluginContext", "ThreadedPlugin"]
