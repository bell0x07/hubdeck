"""Page navigation and switching pages by the focused app. The engine executes these and
sets the states itself; this class only describes them for validation and `hubdeck validate`."""

from __future__ import annotations

from .base import Plugin


class PagePlugin(Plugin):
    name = "page"
    actions = {
        "goto": "open a page (folder); argument: page name",
        "back": "go back to the previous page",
        "home": "go to start_page",
        "auto_on": "switch pages by the focused app again (pages' match)",
        "auto_off": "stop switching pages by the focused app",
        "auto_toggle": "turn switching pages by the focused app on or off",
    }
    states = {
        "auto": "whether pages switch by the focused app",
        "app": "the focused app, e.g. X2.EXE (macOS: Safari)",
        "title": "the focused window's title",
    }

    @classmethod
    def check_action(cls, action, arg):
        if action == "goto" and not isinstance(arg, str):
            return "page.goto needs a page name, e.g. {page: media}"
        return None
