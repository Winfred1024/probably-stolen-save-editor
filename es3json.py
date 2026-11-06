"""
ES3 JSON reader/writer for "Probably Stolen" save files.

The game persists saves through Easy Save 3 (Moodkie) using ES3's own JSON
dialect.  It is *almost* standard JSON but differs in two ways that matter:

  * `Dictionary<int, T>` keys are written bare:  "PhoneClientDict":{8376:{...}}
  * typographic quotes are backslash-escaped:    "\\u201c污灶\\u201d"

Neither is accepted by `json.loads`.  Worse, ES3 serialises doubles with .NET's
"R" format, which is not always the shortest round-trip representation Python
would pick (e.g. `87.939375000000055` vs `87.93937500000006`), so a parse +
re-serialise cycle can never be guaranteed byte-identical for every value.

Since a save editor only ever needs to change a handful of numbers, this module
never re-serialises the whole document.  It parses into a tree that remembers
the character span of every node, then rewrites *only* the spans you touch.
Every other byte of the file -- float formatting, key order, escaping quirks
included -- is preserved verbatim.  That is the safest possible way to write
back a save the game must still load.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator

# --- ES3 lexing quirks -------------------------------------------------------

_WS = " \t\r\n"

# Characters whose backslash-escaping ES3 emits but strict JSON rejects.
_ODD_ESCAPES = "\u201c\u201d\u2018\u2019"
_ESCAPE_MAP = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

# Inverse for writing: characters ES3 escapes when it emits a string.
_WRITE_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "/": "\\/",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\u201c": "\\\u201c",
    "\u201d": "\\\u201d",
}


class ES3Error(ValueError):
    """Raised when a file is not parseable as ES3 JSON."""


def quote(value: str) -> str:
    """Emit a string literal the way ES3 does."""
    out = ['"']
    for ch in value:
        esc = _WRITE_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def literal(value: Any) -> str:
    """Emit any Python scalar as ES3 JSON."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value)) + ".0"
        return repr(value)
    raise TypeError(f"cannot serialise {type(value)!r}")


# --- node tree ---------------------------------------------------------------


class Node:
    """One JSON node plus the [start, end) span it occupies in the source."""

    __slots__ = ("kind", "value", "start", "end", "items", "keys")

    def __init__(self, kind: str, start: int, end: int, value: Any = None):
        self.kind = kind          # object | array | string | number | bool | null
        self.start = start
        self.end = end
        self.value = value        # scalar payload; number -> int|float
        self.items: Any = None    # object: dict[key, Node]; array: list[Node]
        self.keys: Any = None     # object: list[(key, key_start, key_end)]

    # -- navigation ----------------------------------------------------------

    def __getitem__(self, key: Any) -> "Node":
        if self.kind == "array":
            if not isinstance(key, int):
                raise TypeError(f"array needs an integer index, got {key!r}")
            return self.items[key]
        if self.kind != "object":
            raise TypeError(f"cannot index {self.kind} with {key!r}")
        if key in self.items:
            return self.items[key]
        # tolerate str/int mismatch ("1" vs 1) from dictionary keys
        alt = str(key) if not isinstance(key, str) else (
            int(key) if key.lstrip("-").isdigit() else key
        )
        if alt in self.items:
            return self.items[alt]
        raise KeyError(f"{key!r} not in object (keys: {list(self.items)[:8]}...)")

    def get(self, *path: Any, default: Any = ...) -> Any:
        """Walk a path of object keys / array indices; return the node."""
        node = self
        for step in path:
            try:
                node = node[step]
            except (KeyError, IndexError, TypeError):
                if default is ...:
                    raise
                return default
        return node

    def __iter__(self) -> Iterator["Node"]:
        return iter(self.items if self.kind == "array" else self.items.values())

    def __len__(self) -> int:
        if self.kind in ("object", "array"):
            return len(self.items)
        return 0

    def keys_list(self) -> list:
        return [k for k, _, _ in self.keys] if self.kind == "object" else []

    # -- values --------------------------------------------------------------

    def to_python(self) -> Any:
        if self.kind == "object":
            return {k: n.to_python() for k, n in self.items.items()}
        if self.kind == "array":
            return [n.to_python() for n in self.items]
        return self.value

    def as_number(self) -> float:
        if self.kind != "number":
            raise TypeError(f"not a number: {self.kind}")
        return self.value

    def __repr__(self) -> str:
        if self.kind in ("object", "array"):
            return f"<{self.kind} len={len(self)} @{self.start}>"
        return f"<{self.kind} {self.value!r} @{self.start}>"


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def fail(self, message: str) -> ES3Error:
        snippet = self.text[max(0, self.pos - 40): self.pos + 40]
        return ES3Error(f"{message} at char {self.pos}: ...{snippet!r}...")

    def skip_ws(self) -> None:
        text, pos, end = self.text, self.pos, len(self.text)
        while pos < end and text[pos] in _WS:
            pos += 1
        self.pos = pos

    def parse(self) -> Node:
        self.skip_ws()
        node = self.value()
        self.skip_ws()
        if self.pos != len(self.text):
            raise self.fail("trailing data")
        return node

    def value(self) -> Node:
        self.skip_ws()
        if self.pos >= len(self.text):
            raise self.fail("unexpected end of input")
        ch = self.text[self.pos]
        if ch == "{":
            return self.object()
        if ch == "[":
            return self.array()
        if ch == '"':
            return self.string()
        return self.scalar()

    def object(self) -> Node:
        start = self.pos
        self.pos += 1  # {
        node = Node("object", start, -1, value=None)
        node.items = {}
        node.keys = []
        self.skip_ws()
        if self.text[self.pos: self.pos + 1] == "}":
            self.pos += 1
            node.end = self.pos
            return node
        while True:
            self.skip_ws()
            key, key_start = self.key()
            key_end = self.pos
            self.skip_ws()
            if self.text[self.pos: self.pos + 1] != ":":
                raise self.fail("expected ':' after key")
            self.pos += 1
            child = self.value()
            node.items[_key_hashable(key)] = child
            node.keys.append((_key_hashable(key), key_start, key_end))
            self.skip_ws()
            ch = self.text[self.pos: self.pos + 1]
            if ch == ",":
                self.pos += 1
                continue
            if ch == "}":
                self.pos += 1
                break
            raise self.fail("expected ',' or '}' in object")
        node.end = self.pos
        return node

    def key(self) -> tuple[Any, int]:
        start = self.pos
        ch = self.text[self.pos: self.pos + 1]
        if ch == '"':
            node = self.string()
            return node.value, start
        # bare key: integer (ES3 writes Dictionary<int,T> keys unquoted)
        begin = self.pos
        if self.text[self.pos: self.pos + 1] == "-":
            self.pos += 1
        digits = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos == digits:
            raise self.fail("expected string or integer key")
        return int(self.text[begin: self.pos]), start

    def array(self) -> Node:
        start = self.pos
        self.pos += 1  # [
        node = Node("array", start, -1)
        node.items = []
        self.skip_ws()
        if self.text[self.pos: self.pos + 1] == "]":
            self.pos += 1
            node.end = self.pos
            return node
        while True:
            node.items.append(self.value())
            self.skip_ws()
            ch = self.text[self.pos: self.pos + 1]
            if ch == ",":
                self.pos += 1
                continue
            if ch == "]":
                self.pos += 1
                break
            raise self.fail("expected ',' or ']' in array")
        node.end = self.pos
        return node

    def string(self) -> Node:
        start = self.pos
        text = self.text
        self.pos += 1  # opening quote
        chunks: list[str] = []
        while True:
            if self.pos >= len(text):
                raise self.fail("unterminated string")
            ch = text[self.pos]
            if ch == '"':
                self.pos += 1
                break
            if ch == "\\":
                self.pos += 1
                esc = text[self.pos: self.pos + 1]
                if esc in _ESCAPE_MAP:
                    chunks.append(_ESCAPE_MAP[esc])
                    self.pos += 1
                elif esc == "u":
                    hexpart = text[self.pos + 1: self.pos + 5]
                    try:
                        cp = int(hexpart, 16)
                    except ValueError:
                        raise self.fail("bad \\u escape") from None
                    self.pos += 5
                    # combine surrogate pairs
                    if 0xD800 <= cp <= 0xDBFF and text[self.pos: self.pos + 2] == "\\u":
                        low = int(text[self.pos + 2: self.pos + 6], 16)
                        if 0xDC00 <= low <= 0xDFFF:
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)
                            self.pos += 6
                    chunks.append(chr(cp))
                elif esc in _ODD_ESCAPES:
                    # ES3's stray backslash before a typographic quote
                    chunks.append(esc)
                    self.pos += 1
                else:
                    raise self.fail(f"unknown escape '\\{esc}'")
            else:
                # fast path: consume a run of ordinary characters
                nxt = self.pos
                while nxt < len(text) and text[nxt] not in '"\\':
                    nxt += 1
                chunks.append(text[self.pos: nxt])
                self.pos = nxt
        return Node("string", start, self.pos, value="".join(chunks))

    def scalar(self) -> Node:
        start = self.pos
        text = self.text
        while self.pos < len(text) and text[self.pos] not in ",:]}" + _WS:
            self.pos += 1
        token = text[start: self.pos]
        if not token:
            raise self.fail("empty value")
        if token == "true":
            return Node("bool", start, self.pos, value=True)
        if token == "false":
            return Node("bool", start, self.pos, value=False)
        if token == "null":
            return Node("null", start, self.pos, value=None)
        if token in ("NaN", "Infinity", "-Infinity"):
            return Node("number", start, self.pos, value={
                "NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")
            }[token])
        try:
            if any(c in token for c in ".eE") and token.lstrip("-").replace(".", "", 1).isdigit() is not None:
                return Node("number", start, self.pos, value=float(token))
            return Node("number", start, self.pos, value=int(token))
        except ValueError:
            raise self.fail(f"unsupported token {token!r}") from None


def _key_hashable(key: Any) -> Any:
    return key


def unwrap(node: "Node") -> "Node":
    """Peel ES3's `{"__type": ..., "value": ...}` wrappers until a real value shows.

    ES3 stores root-level and polymorphic values with their type attached; the
    interesting payload is always in `value`.
    """
    seen = 0
    while (
        node.kind == "object"
        and len(node) == 2
        and node.items.get("__type") is not None
        and node.items.get("value") is not None
        and seen < 8
    ):
        node = node.items["value"]
        seen += 1
    return node


# --- document ----------------------------------------------------------------


class Document:
    """A parsed ES3 file that can be edited and written back byte-precisely."""

    def __init__(self, text: str, path: str | None = None):
        if text.startswith("\ufeff"):
            self.bom = True
            text = text[1:]
        else:
            self.bom = False
        self.text = text
        self.path = path
        self.root = _Parser(text).parse()
        self._edits: dict[int, tuple[int, int, str]] = {}

    # -- loading / saving ----------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "Document":
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            return cls(fh.read(), path)

    def render(self) -> str:
        if not self._edits:
            return ("\ufeff" if self.bom else "") + self.text
        # splice from the end so earlier offsets stay valid
        out = self.text
        for start, end, replacement in sorted(self._edits.values(), key=lambda e: e[0], reverse=True):
            out = out[:start] + replacement + out[end:]
        return ("\ufeff" if self.bom else "") + out

    def save(self, path: str | None = None, backup: bool = True) -> str:
        target = path or self.path
        if not target:
            raise ValueError("no path to save to")
        if backup:
            _make_backup(target)
        data = self.render()
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(data)
        return target

    # -- editing -------------------------------------------------------------

    def set(self, node: Node, value: Any) -> None:
        """Schedule a replacement of `node`'s span with `value`."""
        if node.kind in ("object", "array"):
            raise TypeError("structural replacement is not supported")
        self._edits[node.start] = (node.start, node.end, literal(value))

    def set_any(self, node: Node, value: Any) -> None:
        """Replace any node (including whole arrays) with serialised `value`."""
        self._edits[node.start] = (node.start, node.end, dumps(value))

    def set_text(self, node: Node, text: str) -> None:
        """Replace a node with already-serialised JSON text."""
        if not text:
            raise ValueError("empty replacement")
        self._edits[node.start] = (node.start, node.end, text)

    def slice(self, node: Node) -> str:
        """The exact original source text of a node."""
        return self.text[node.start:node.end]

    def set_path(self, path: Iterable[Any], value: Any) -> None:
        self.set(self.root.get(*path), value)

    def dirty(self) -> bool:
        return bool(self._edits)

    def reset(self) -> None:
        self._edits.clear()

    # -- verification --------------------------------------------------------

    def verify(self) -> list[str]:
        """Re-parse our own output and confirm exactly the edited nodes moved."""
        problems: list[str] = []
        rendered = self.render()
        try:
            again = Document(rendered)
        except ES3Error as exc:  # pragma: no cover
            return [f"output does not parse: {exc}"]
        before = self.root.to_python()
        after = again.root.to_python()
        if _diff_paths(before, after):
            problems.append("edited output differs from the intended change")
        return problems


def _diff_paths(a: Any, b: Any, path: tuple = ()) -> list[tuple]:
    """Paths where two plain-Python trees differ."""
    out: list[tuple] = []
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        out.append(path + (("type", type(a).__name__, type(b).__name__),))
    elif isinstance(a, dict):
        for key in a.keys() | b.keys():
            if key not in a or key not in b:
                out.append(path + (key,))
            else:
                out.extend(_diff_paths(a[key], b[key], path + (key,)))
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(path + (("len", len(a), len(b)),))
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(_diff_paths(x, y, path + (i,)))
    elif a != b:
        out.append(path)
    return out


def _make_backup(path: str) -> str:
    import os
    import time

    stamp = time.strftime("%Y%m%d-%H%M%S")
    root, ext = os.path.splitext(path)
    backup = f"{root}.bak-{stamp}{ext}"
    if not os.path.exists(backup):
        with open(path, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
    return backup


# --- convenience -------------------------------------------------------------


def loads(text: str) -> Any:
    return Document(text).root.to_python()


def load_file(path: str) -> Any:
    return Document.load(path).root.to_python()


def dumps(obj: Any) -> str:
    """Full re-serialisation; only used by tests, never to write saves."""
    from collections import OrderedDict
    if isinstance(obj, dict):
        return "{" + ",".join(
            (str(k) if isinstance(k, int) else quote(str(k))) + ":" + dumps(v) for k, v in obj.items()
        ) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(dumps(v) for v in obj) + "]"
    return literal(obj)


if __name__ == "__main__":
    import glob
    import os
    import sys

    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    bad = 0
    for name in sorted(glob.glob(os.path.join(folder, "*.es3"))):
        try:
            doc = Document.load(name)
            same = doc.render() == open(name, encoding="utf-8-sig").read()
            mark = "OK  " if same else "DIFF"
        except ES3Error as exc:
            mark, same = "FAIL", False
            print(f"      {exc}")
        bad += not same
        print(f"{mark} {os.path.basename(name)}")
    print(f"\n{len(glob.glob(os.path.join(folder, '*.es3'))) - bad} untouched file(s) reproduce exactly")
