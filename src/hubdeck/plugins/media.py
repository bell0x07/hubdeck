"""What's playing (Windows): the media session the media keys control, as states.

    keys:
      9:
        action: {media: play_pause}          # the media key (keyboard plugin)
        state: media.playing
        face:    {icon: player-play, label: "{media.app}"}
        face_on: {icon: player-pause, label: "{media.app}"}
"""

from __future__ import annotations

from ..platforms import Unsupported, current
from .base import ThreadedPlugin

FIELDS = ("playing", "app", "title", "artist")


class MediaPlugin(ThreadedPlugin):
    name = "media"
    platforms = ("win32",)
    interval = 0.5
    states = {
        "playing": "true while the app the media keys control plays, false while it's paused; "
                   "unknown when no app has media open",
        "app": "that app's name, e.g. Spotify",
        "title": "the title of what it plays",
        "artist": "the artist of what it plays",
    }

    def setup(self):
        self.reader = None
        self.published: dict[str, object] = {}
        self.failing = False
        try:
            self.reader = current().media()        # on this thread: WinRT and COM
        except Unsupported as e:
            self.log.warning("%s", e)

    def teardown(self):
        if self.reader:
            self.reader.close()

    def tick(self):
        if self.reader is None:
            return
        try:
            now = self.reader.read()
            self.failing = False
        except Exception:                          # e.g. the app closed its session mid-read: keep what's shown
            (self.log.debug if self.failing else self.log.warning)("could not read what's playing", exc_info=True)
            self.failing = True
            return
        for field in FIELDS:
            value = getattr(now, field) if now else None
            if field not in self.published or self.published[field] != value:
                self.published[field] = value
                self.ctx.set_state(field, value)
