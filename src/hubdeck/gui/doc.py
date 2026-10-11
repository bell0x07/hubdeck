"""Edit config.yaml in place for the GUI, keeping the user's comments and layout.

The GUI talks in plain JSON-able values; this module converts them to and
from ruamel.yaml round-trip nodes, validates the whole file with the normal
loader before writing, and writes atomically.
"""

from __future__ import annotations

import difflib
import io
import os
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from ..config import ConfigError, load_config
from .. import paths

SECTIONS = ("keys", "dial", "knobs", "rollers", "faders")
GLOBALS = ("device", "modules", "defaults", "lighting")


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 120
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def to_plain(node: Any) -> Any:
    """ruamel node -> JSON-able value. `!secret x` becomes {"$secret": "x"}."""
    if isinstance(node, TaggedScalar):
        return {"$secret": str(node.value)} if node.tag.value == "!secret" else str(node.value)
    if isinstance(node, dict):
        return {str(k): to_plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [to_plain(v) for v in node]
    if isinstance(node, str):
        return str(node)
    return node


_NEEDS_QUOTES = re.compile(r"^[#{\[*&!%@`'\"|>?:,-]|: | #|^(yes|no|on|off|true|false|null|~)$|^[-+.\d]", re.I)


def _scalar(value: Any) -> Any:
    """Strings that YAML would need quoted get double quotes (the style hubdeck's examples use)."""
    if isinstance(value, str) and (value == "" or _NEEDS_QUOTES.search(value) or "{" in value):
        return DoubleQuotedScalarString(value)
    return value


def _norm(line: str) -> str:
    """A line with alignment padding removed, to tell real edits from re-indentation."""
    indent = len(line) - len(line.lstrip())
    return line[:indent] + re.sub(r" {2,}", " ", line.strip())


def keep_original_spacing(old: str, new: str) -> str:
    """ruamel re-flows hand-aligned lines (`face:    {...}`); keep the user's spacing on lines whose
    content didn't change."""
    a, b = old.splitlines(), new.splitlines()
    out = list(b)
    sm = difflib.SequenceMatcher(a=[_norm(x) for x in a], b=[_norm(x) for x in b], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out[j1:j2] = a[i1:i2]
    return "\n".join(out) + ("\n" if new.endswith("\n") else "")


def merge_into(node: CommentedMap, value: dict) -> CommentedMap:
    """Update an existing mapping in place from a plain dict, so keys that survive keep their
    position, style and comments. Keys missing from `value` are removed."""
    for key in [k for k in node if str(k) not in {str(x) for x in value}]:
        del node[key]
    existing = {str(k): k for k in node}
    for k, v in value.items():
        key = existing.get(str(k), int(k) if isinstance(k, str) and k.isdigit() and
                           any(isinstance(x, int) for x in node) else k)
        old = node.get(key)
        if isinstance(old, CommentedMap) and isinstance(v, dict) and set(v) != {"$secret"}:
            merge_into(old, v)
        elif to_plain(old) != v or key not in node:
            node[key] = from_plain(v)
    return node


def _is_leaf(value: Any) -> bool:
    return not isinstance(value, (dict, list)) or (isinstance(value, dict) and "$secret" in value)


def from_plain(value: Any, key_ints: bool = False) -> Any:
    """JSON-able value -> ruamel node. Small mappings of scalars are written in flow style."""
    if isinstance(value, dict):
        if set(value) == {"$secret"}:
            return TaggedScalar(value["$secret"], tag="!secret")
        m = CommentedMap()
        for k, v in value.items():
            key = int(k) if key_ints and str(k).isdigit() else k
            m[key] = from_plain(v, key_ints=False)
        if value and all(_is_leaf(v) for v in value.values()) and len(value) <= 6:
            m.fa.set_flow_style()
        return m
    if isinstance(value, list):
        s = CommentedSeq(from_plain(v) for v in value)
        if all(_is_leaf(v) for v in value):
            s.fa.set_flow_style()
        return s
    return _scalar(value)


class ConfigDoc:
    def __init__(self, path: Path, secrets: Path | None = None):
        self.path = Path(path)
        self.secrets = Path(secrets) if secrets else paths.secrets_path(self.path)
        self.yaml = _yaml()
        self.data: CommentedMap = CommentedMap()
        self.load()

    # -- io ----------------------------------------------------------------
    def load(self):
        raw = self.path.read_bytes() if self.path.exists() else b""
        self.newline = "\r\n" if b"\r\n" in raw else "\n"      # keep the file's own line endings
        text = raw.decode("utf-8").replace("\r\n", "\n")
        self.original = text
        self.data = self.yaml.load(text) or CommentedMap()

    def text(self) -> str:
        out = io.StringIO()
        self.yaml.dump(self.data, out)
        return keep_original_spacing(self.original, out.getvalue())

    def validate(self, text: str) -> str | None:
        """Error message for config text, or None if valid. Checked next to the real
        config so relative image paths resolve the same way."""
        tmp = self.path.with_name(f".{self.path.stem}.gui-check.yaml")
        try:
            tmp.write_text(text, encoding="utf-8")
            load_config(tmp, self.secrets)
            return None
        except ConfigError as e:
            return str(e).replace(str(tmp), str(self.path))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def save(self, text: str | None = None) -> str | None:
        """Validate and write. Returns an error message (and keeps the file) if invalid."""
        text = self.text() if text is None else text
        err = self.validate(text)
        if err:
            self.load()           # drop the rejected in-memory change
            return err
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_bytes(text.replace("\r\n", "\n").replace("\n", self.newline).encode("utf-8"))
        os.replace(tmp, self.path)
        self.load()
        return None

    # -- structure ---------------------------------------------------------
    @property
    def single_page(self) -> bool:
        return "pages" not in self.data

    @property
    def start_page(self) -> str:
        return str(self.data.get("start_page", "main"))

    def page_names(self) -> list[str]:
        if self.single_page:
            return [self.start_page]
        return [str(p) for p in (self.data.get("pages") or {})]

    def to_pages(self):
        """Turn the single-page shorthand into a `pages:` section (same nodes, comments kept)."""
        if not self.single_page:
            return
        page = CommentedMap()
        for s in SECTIONS:
            if s in self.data:
                page[s] = self.data.pop(s)
        pages = CommentedMap()
        pages[self.start_page] = page
        keys = list(self.data.keys())
        pos = keys.index("start_page") + 1 if "start_page" in keys else len(keys)
        self.data.insert(pos, "pages", pages)

    def page_node(self, page: str, create: bool = False) -> CommentedMap | None:
        if self.single_page:
            if page == self.start_page:
                return self.data
            if not create:
                return None
            self.to_pages()
        pages = self.data.setdefault("pages", CommentedMap())
        if page not in pages and create:
            pages[page] = CommentedMap()
        return pages.get(page)

    # -- items -------------------------------------------------------------
    def get_item(self, page: str, section: str, num: int | None) -> Any:
        node = self.page_node(page)
        if node is None or section not in node:
            return None
        sec = node[section]
        if section == "dial":
            return sec
        return (sec or {}).get(int(num))

    def set_item(self, page: str, section: str, num: int | None, value: Any):
        """value: plain dict, a ruamel node, or None to delete."""
        node = self.page_node(page, create=True)
        existing = self.get_item(page, section, num)
        if isinstance(value, dict) and not isinstance(value, CommentedMap) and isinstance(existing, CommentedMap):
            merge_into(existing, value)          # edit in place: keeps comments inside the item
            return
        if value is not None and not isinstance(value, (CommentedMap, CommentedSeq)):
            value = from_plain(value)
        if section == "dial":
            if value is None:
                node.pop("dial", None)
            else:
                node["dial"] = value
            return
        sec = node.get(section)
        if sec is None:
            if value is None:
                return
            sec = node[section] = CommentedMap()
        num = int(num)
        if value is None:
            sec.pop(num, None)
            if not sec:
                node.pop(section, None)
            return
        if num in sec:
            sec[num] = value
        else:
            pos = sum(1 for k in sec if isinstance(k, int) and k < num)
            sec.insert(pos, num, value)

    def get_global(self, name: str) -> Any:
        return self.data.get(name)

    def set_global(self, name: str, value: Any):
        if value is None or value == {}:
            self.data.pop(name, None)
            return
        existing = self.data.get(name)
        if isinstance(value, dict) and not isinstance(value, CommentedMap) and isinstance(existing, CommentedMap):
            merge_into(existing, value)
            return
        if not isinstance(value, (CommentedMap, CommentedSeq)):
            value = from_plain(value, key_ints=False)
        if name in self.data:
            self.data[name] = value
        else:
            keys = list(self.data.keys())
            anchor = next((k for k in ("start_page", "pages", "keys") if k in keys), None)
            self.data.insert(keys.index(anchor) if anchor else len(keys), name, value)

    # -- pages -------------------------------------------------------------
    def add_page(self, name: str):
        self.page_node(name, create=True)

    def rename_page(self, old: str, new: str):
        self.to_pages()
        pages = self.data["pages"]
        items = list(pages.items())
        pages.clear()
        for k, v in items:
            pages[new if k == old else k] = v
        if self.start_page == old:
            self.data["start_page"] = new

    def delete_page(self, name: str):
        if self.single_page or name == self.start_page:
            raise ValueError("the start page can't be deleted")
        self.data["pages"].pop(name, None)

    # -- yaml snippets -----------------------------------------------------
    def node_yaml(self, node: Any) -> str:
        if node is None:
            return ""
        out = io.StringIO()
        self.yaml.dump(node, out)
        return out.getvalue()

    def parse_yaml(self, text: str) -> Any:
        return self.yaml.load(text) if text.strip() else None
