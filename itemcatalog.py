"""
The item catalogue ("物品百科").

The game keeps its item database in code, not in an asset file, so the only
authoritative source for an item's *complete* data (display name, description,
grid shape, sprite paths, type tags, value) is an item instance the game itself
wrote into a save.  This module therefore builds a catalogue out of every item
instance found in the player's saves -- including the game's automatic history
snapshots -- and keeps it up to date every time a save is opened.

Each catalogue entry stores the exact JSON text of one real instance, so adding
an item to an inventory is a byte-faithful clone with a new uuid, never a
guess at the game's data model.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import es3json

CATALOG_VERSION = 1
CATALOG_NAME = "item_catalog.json"


@dataclass
class Entry:
    identifier: str
    name: str
    types: list[str]
    value: int
    width: int
    height: int
    description: str
    flavor: str
    template: str
    seen: int = 1

    @property
    def category(self) -> str:
        return self.types[0] if self.types else "未分类"

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "types": self.types,
            "value": self.value,
            "width": self.width,
            "height": self.height,
            "description": self.description,
            "flavor": self.flavor,
            "template": self.template,
            "seen": self.seen,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(
            identifier=d["identifier"],
            name=d.get("name", ""),
            types=list(d.get("types") or []),
            value=int(d.get("value") or 0),
            width=int(d.get("width") or 1),
            height=int(d.get("height") or 1),
            description=d.get("description") or "",
            flavor=d.get("flavor") or "",
            template=d.get("template") or "",
            seen=int(d.get("seen") or 1),
        )


FACTORY_FILE = "item_factory.json"

# tags the game only writes after an item has lived in the world, never at
# creation time -- they must not come along when a catalogue item is built.
RUNTIME_ONLY_TAGS = (
    "STOLEN_TAG", "STOLEN_VALUE_INT", "STOLEN_LEVEL", "STOLEN_SOURCE_STRING",
    "BOUGHT_PRICE_TAG",
    "GROWING_TAG", "GROWING_PROGRESS", "PLANT_HEALTH_INT", "ASSOCIATED_HYDROPONIC_ID",
    "INSPECTED_TAG", "LABELED_TAG",
)


def load_factory(path: str | Path | None = None) -> dict:
    """identifier -> how the game's factory builds that item (extracted from IL)."""
    target = Path(path) if path else Path(__file__).with_name(FACTORY_FILE)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {d["identifier"]: d for d in data if d.get("identifier")}


def defaultize_template(text: str, spec: dict | None) -> str:
    """Rebuild a template the way the item factory would: no world state.

    Catalogue templates are copied from items that lived in a save, so they can
    carry stolen flags, a purchase price, growth progress and so on.  A freshly
    built item has none of that, and its type list / value / durability come
    from the factory.
    """
    try:
        doc = es3json.Document(text)
    except es3json.ES3Error:
        return text

    values = doc.root.get("_values", default=None)
    keys = doc.root.get("_keys", default=None)
    if values is None or keys is None:
        return text

    drop = set(RUNTIME_ONLY_TAGS)
    entries = []
    for entry in values.items:
        name = _s(entry, "<identifier>k__BackingField")
        if name in drop:
            continue
        entries.append((name, doc.slice(entry)))

    maximum = None
    for name, entry_text in entries:
        if name in ("MAX_DURABILITY_TAG", "DEFAULT_DURABILITY_TAG"):
            maximum = _n(es3json.Document(entry_text).root, "valueInt") or maximum

    factory_value = None
    if spec:
        raw = spec.get("value")
        if isinstance(raw, (int, float)):
            factory_value = int(raw)

    out = []
    for name, entry_text in entries:
        if name == "DURABILITY_TAG" and maximum:
            entry = es3json.Document(entry_text)
            target = entry.root.get("valueInt", default=None)
            if target is not None and int(target.value) != maximum:
                entry.set(target, maximum)
                entry_text = entry.render()
        out.append(entry_text.strip())

    doc.set_text(values, "[" + ",".join(out) + "]")
    doc.set_any(keys, [n for n, _t in entries])

    if spec:
        types = spec.get("types") or []
        if types:
            types_node = doc.root.get("itemTypes", default=None)
            if types_node is not None:
                doc.set_any(types_node, list(types))
        if factory_value is not None:
            value_node = doc.root.get("unitValue", default=None)
            if value_node is not None:
                doc.set(value_node, factory_value)
        base_node = doc.root.get("unitBaseValue", default=None)
        if base_node is not None:
            doc.set(base_node, 0)

    return doc.render()


STOLEN_KEYS = ("STOLEN_TAG", "STOLEN_VALUE_INT", "STOLEN_LEVEL", "STOLEN_SOURCE_STRING")


def normalize_template(text: str) -> str:
    """Strip per-instance dirt so a catalogue item behaves like a fresh one.

    Templates are copied from whatever instance the game wrote into a save, so
    they carry that instance's state: a stolen flag, the price the previous
    owner paid, and a half-worn durability.  Adding such a template produces a
    stolen, worn item with a wrong price.  This resets all of that:

      * stolen tags removed
      * BOUGHT_PRICE_TAG set to the item's normal unitValue
      * durability restored to its maximum
    """
    try:
        doc = es3json.Document(text)
    except es3json.ES3Error:
        return text
    values = doc.root.get("_values", default=None)
    keys = doc.root.get("_keys", default=None)
    if values is None or keys is None:
        return text

    unit_value = _n(doc.root, "unitValue")

    entries = []
    changed = False
    for entry in values.items:
        name = _s(entry, "<identifier>k__BackingField")
        if name in STOLEN_KEYS:
            changed = True
            continue
        entries.append((name, doc.slice(entry)))

    names = [n for n, _t in entries]

    maximum = None
    for name, entry_text in entries:
        if name in ("MAX_DURABILITY_TAG", "DEFAULT_DURABILITY_TAG"):
            maximum = _n(es3json.Document(entry_text).root, "valueInt") or maximum

    out_texts = []
    for name, entry_text in entries:
        if name == "BOUGHT_PRICE_TAG" and unit_value > 0:
            entry = es3json.Document(entry_text)
            target = entry.root.get("valueInt", default=None)
            if target is not None and int(target.value) != unit_value:
                entry.set(target, unit_value)
                entry_text = entry.render()
                changed = True
        elif name == "DURABILITY_TAG" and maximum:
            entry = es3json.Document(entry_text)
            target = entry.root.get("valueInt", default=None)
            if target is not None and int(target.value) != maximum:
                entry.set(target, maximum)
                entry_text = entry.render()
                changed = True
        out_texts.append(entry_text)

    if not changed:
        return text
    doc.set_text(values, "[" + ",".join(t.strip() for t in out_texts) + "]")
    doc.set_any(keys, names)
    return doc.render()


class Catalog:
    """Item templates keyed by identifier, persisted next to the tool."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path(__file__).with_name(CATALOG_NAME)
        self.entries: dict[str, Entry] = {}
        self._dirty = False
        self.load()

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for raw in data.get("items", []):
            try:
                entry = Entry.from_dict(raw)
            except Exception:
                continue
            if entry.identifier and entry.template:
                self.entries[entry.identifier] = entry

    def save(self) -> None:
        payload = {
            "version": CATALOG_VERSION,
            "note": "Probably Stolen 物品模板库：由存档里真实存在的物品实例生成，添加物品时按原样克隆。",
            "items": [self.entries[k].to_dict() for k in sorted(self.entries)],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False

    # -- harvesting ----------------------------------------------------------

    def harvest_profile(self, profile, include_history: bool = True) -> int:
        """Scan every save in a profile folder and add unknown items. Returns new count."""
        added = 0
        pattern = "save_*.es3" if include_history else "save_[0-9].es3"
        for path in sorted(Path(profile.folder).glob(pattern)):
            try:
                doc = es3json.Document.load(str(path))
            except Exception:
                continue
            player = doc.root.get("playerStore", default=None)
            if player is None:
                continue
            value = player.get("value", default=None)
            if value is None:
                continue
            for key in value.keys_list():
                if not str(key).endswith("InvJSON"):
                    continue
                node = value.get(key, default=None)
                if node is None or node.kind != "string":
                    continue
                added += self.harvest_inventory_text(node.value)
        if added:
            self.save()
        return added

    def harvest_inventory_text(self, text: str) -> int:
        try:
            doc = es3json.Document(text)
        except es3json.ES3Error:
            return 0
        items = doc.root.get("saveItems", default=None)
        if items is None or items.kind != "array":
            return 0
        added = 0
        for node in items.items:
            identifier = _s(node, "identifier")
            if not identifier:
                continue
            existing = self.entries.get(identifier)
            if existing is not None:
                existing.seen += 1
                value = _n(node, "unitValue")
                if value > existing.value:
                    existing.value = value
                continue
            shape = node.get("itemModifiedShape", default=None) or node.get("itemShape", default=None)
            self.entries[identifier] = Entry(
                identifier=identifier,
                name=_s(node, "name"),
                types=[t.value for t in node.get("itemTypes").items]
                if node.get("itemTypes", default=None) is not None
                else [],
                value=_n(node, "unitValue"),
                width=max(1, _n(shape, "<width>k__BackingField")) if shape is not None else 1,
                height=max(1, _n(shape, "<height>k__BackingField")) if shape is not None else 1,
                description=_s(node, "shortDescription"),
                flavor=_s(node, "flavorText"),
                template=normalize_template(doc.slice(node)),
            )
            added += 1
        return added

    def normalize_all(self) -> int:
        """Re-clean every stored template; returns how many changed."""
        changed = 0
        for entry in self.entries.values():
            clean = normalize_template(entry.template)
            if clean != entry.template:
                entry.template = clean
                changed += 1
        if changed:
            self.save()
        return changed

    # -- queries -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, identifier: str) -> Entry | None:
        return self.entries.get(identifier)

    def all(self) -> list[Entry]:
        return [self.entries[k] for k in sorted(self.entries, key=lambda k: (self.entries[k].name or k))]

    def categories(self) -> list[str]:
        cats = {e.category for e in self.entries.values()}
        return sorted(cats)


def _n(node, key: str, default: int = 0) -> int:
    if node is None:
        return default
    child = node.get(key, default=None)
    if child is None or child.kind != "number":
        return default
    return int(child.value)


def _s(node, key: str, default: str = "") -> str:
    child = node.get(key, default=None)
    if child is None or child.kind != "string":
        return default
    return child.value
