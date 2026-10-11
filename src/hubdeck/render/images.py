"""Images and animated GIFs for faces, from files or http(s) URLs.

URLs are downloaded in the background (size limit, timeout) into a cache
folder; until then `get` returns None and the face draws without the image.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

log = logging.getLogger("hubdeck.images")

MAX_DOWNLOAD = 10 * 1024 * 1024
MAX_FRAMES = 300
MIN_FRAME_SECONDS = 0.02        # GIFs with 0/10 ms delays are meant to be ~50 fps at most


def is_url(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


@dataclass
class LoadedImage:
    frames: list[Image.Image]   # RGBA
    durations: list[float]      # seconds per frame

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def frame_at(self, t: float) -> int:
        """Index of the frame to show at time t (seconds, looping)."""
        if not self.animated:
            return 0
        t %= sum(self.durations)
        for i, d in enumerate(self.durations):
            if t < d:
                return i
            t -= d
        return len(self.frames) - 1


def load_image(path: Path) -> LoadedImage:
    with Image.open(path) as im:
        frames, durations = [], []
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.convert("RGBA"))
            durations.append(max(MIN_FRAME_SECONDS, frame.info.get("duration", 100) / 1000))
            if len(frames) >= MAX_FRAMES:
                break
    return LoadedImage(frames, durations)


class ImageStore:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._images: dict[str, LoadedImage | None] = {}   # None = failed
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self.changed = threading.Event()   # set when a download finished

    def cache_path(self, url: str) -> Path:
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        suffix = suffix if suffix in (".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp") else ".img"
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + suffix)

    def get(self, src: str) -> LoadedImage | None:
        with self._lock:
            if src in self._images:
                return self._images[src]
        if is_url(src):
            cached = self.cache_path(src)
            if not cached.exists():
                self._start_download(src, cached)
                return None
            path = cached
        else:
            path = Path(src).expanduser()
            self._forget_missing()
        try:
            img = load_image(path)
            log.info("loaded image %s (%d frame%s)", src, len(img.frames), "" if len(img.frames) == 1 else "s")
        except Exception as e:
            log.warning("cannot load image %s: %s", src, e)
            img = None
        with self._lock:
            self._images[src] = img
        return img

    def _start_download(self, url: str, dest: Path):
        with self._lock:
            if url in self._pending:
                return
            self._pending.add(url)
        threading.Thread(target=self._download, args=(url, dest), daemon=True, name="hubdeck-download").start()

    def _download(self, url: str, dest: Path):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hubdeck"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(MAX_DOWNLOAD + 1)
            if len(data) > MAX_DOWNLOAD:
                raise ValueError(f"larger than {MAX_DOWNLOAD // (1024 * 1024)} MB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            load_image(tmp)            # make sure it's a usable image before keeping it
            tmp.replace(dest)
            log.info("downloaded %s", url)
        except Exception as e:
            log.warning("cannot download image %s: %s", url, e)
            with self._lock:
                self._images[url] = None
        finally:
            with self._lock:
                self._pending.discard(url)
            self.changed.set()

    def _forget_missing(self):
        """Drop files that are gone, e.g. pictures replaced by a newer `hubdeck send image`."""
        with self._lock:
            for src in [s for s in self._images if not is_url(s) and not Path(s).expanduser().exists()]:
                del self._images[src]

    def forget(self):
        """Drop loaded images (config reload: files may have changed)."""
        with self._lock:
            self._images.clear()
