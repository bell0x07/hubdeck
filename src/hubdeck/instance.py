"""Only one hubdeck may drive the modules at a time."""

from __future__ import annotations


def acquire() -> bool:
    """True if this is the only running hubdeck."""
    from .platforms import current
    return current().acquire_single_instance()
