"""What's playing: Windows' media sessions (what the volume flyout shows and the media keys
control), through pywinrt."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from uuid import UUID

from ..base import MediaReader, NowPlaying

IID_ISHELLITEM = UUID("43826d1e-e718-42ee-bc55-a1e261c37bfe")
SIGDN_NORMALDISPLAY = 0
VT_RELEASE, VT_GET_DISPLAY_NAME = 2, 5           # IShellItem's vtable


def shell_name(app_id: str) -> str | None:
    """The app's name in the Start menu (shell:AppsFolder), or None if it isn't there.
    Needs COM on the calling thread."""
    shell32 = ctypes.WinDLL("shell32")              # own function prototypes, apart from ctypes.windll's
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_void_p)]
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    iid = (ctypes.c_char * 16).from_buffer_copy(IID_ISHELLITEM.bytes_le)
    item = ctypes.c_void_p()
    if shell32.SHCreateItemFromParsingName(f"shell:AppsFolder\\{app_id}", None, ctypes.byref(iid),
                                           ctypes.byref(item)) < 0 or not item:
        return None
    vtable = ctypes.cast(item, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[VT_RELEASE])
    get_name = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_void_p))(vtable[VT_GET_DISPLAY_NAME])
    try:
        name = ctypes.c_void_p()
        if get_name(item, SIGDN_NORMALDISPLAY, ctypes.byref(name)) < 0 or not name:
            return None
        try:
            return ctypes.wstring_at(name.value) or None
        finally:
            ole32.CoTaskMemFree(name)
    finally:
        release(item)


def app_name(app_id: str, lookup=shell_name) -> str:
    """What to call the app behind a session. Sessions name it by its AppUserModelID: the Start
    menu's id for apps that have one (Brave, MSEdge, Microsoft.ZuneMusic_8wekyb3d8bbwe!...),
    otherwise the process (Spotify.exe)."""
    name = lookup(app_id)
    if name:
        return name
    return app_id[:-4] if app_id.lower().endswith(".exe") else app_id


class WinMedia(MediaReader):
    def __init__(self):
        from winrt.runtime import MTA, init_apartment
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        init_apartment(MTA)                          # this thread's COM, for WinRT and the Start menu
        self.manager = Manager.request_async().get()
        self.names: dict[str, str] = {}
        self.last: NowPlaying | None = None

    def close(self):
        from winrt.runtime import uninit_apartment
        uninit_apartment()

    def read(self) -> NowPlaying | None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status
        session = self.manager.get_current_session()
        status = session and session.get_playback_info().playback_status
        if session is None or status == Status.CLOSED:
            self.last = None
            return None
        app_id = session.source_app_user_model_id
        if app_id not in self.names:
            self.names[app_id] = app_name(app_id)
        playing = status == Status.PLAYING
        if status == Status.CHANGING:                # between tracks: keep the icon as it was
            playing = self.last is not None and self.last.app == self.names[app_id] and self.last.playing
        props = session.try_get_media_properties_async().get()
        self.last = NowPlaying(self.names[app_id], playing, props.title or None, props.artist or None)
        return self.last
