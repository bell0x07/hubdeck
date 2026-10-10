"""Page navigation. The engine executes these itself; this class only
describes them for validation and `hubdeck validate`."""

from __future__ import annotations

from .base import Plugin


class PagePlugin(Plugin):
    name = "page"
    actions = {
        "goto": "open a page (folder); argument: page name",
        "back": "go back to the previous page",
        "home": "go to start_page",
    }

    @classmethod
    def check_action(cls, action, arg):
        if action == "goto" and not isinstance(arg, str):
            return "page.goto needs a page name, e.g. {page: media}"
        return None
