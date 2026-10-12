"""Only one hubdeck may drive the modules at a time."""

from __future__ import annotations

import logging
import os
import sys
import time

log = logging.getLogger("hubdeck")


def acquire() -> bool:
    """True if this is the only running hubdeck."""
    from .platforms import current
    return current().acquire_single_instance()


def stop_other_copies() -> bool:
    """Stop other processes of this hubdeck-tray.exe (same file): an older version that
    doesn't know `quit`. Only for the installed app's own launcher, never for a shared
    python executable."""
    import psutil
    if os.path.basename(sys.executable).lower() != "hubdeck-tray.exe":
        return False
    me, exe = os.getpid(), os.path.normcase(os.path.realpath(sys.executable))
    stopped = False
    for proc in psutil.process_iter(["pid", "exe"]):
        try:
            if (proc.info["pid"] != me and proc.info["exe"]
                    and os.path.normcase(os.path.realpath(proc.info["exe"])) == exe):
                proc.terminate()
                stopped = True
        except psutil.Error:
            continue
    return stopped


def replace_running(timeout: float = 15) -> bool:
    """Ask the hubdeck that's running to quit, and wait until this one may start. The Windows
    installer starts the new version this way: Windows swaps the files under a running
    hubdeck, which would otherwise go on running the old version."""
    from .control import ControlError, send
    try:
        send({"cmd": "quit"})
    except ControlError as e:          # a version from before `quit`, or not answering
        if not stop_other_copies():
            log.warning("could not ask the running hubdeck to quit: %s", e)
            return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if acquire():
            return True
    log.warning("the running hubdeck didn't quit within %ss", timeout)
    return False
