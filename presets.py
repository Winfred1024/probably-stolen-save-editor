"""
Quick-add presets: a bag frozen into a reusable **tree**, nesting included.

A bag is not a flat list: storage bays, machine bays, plates and fridges hold
their own items, and those hold more items.  Capturing only the top level would
give you a pile of empty containers.  So a preset stores the whole tree, and
each node keeps the grid position it had inside its parent -- replaying the
captured positions reproduces the original layout exactly instead of guessing
where things go.

Only the roots are placed freely, into whatever bag you target.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import es3json
import inventory

PRESET_FILE = "presets.json"
VERSION = 2


@dataclass
class PresetNode:
    identifier: str = ""
    name: str = ""
    template: str = ""
    x: int = 0
    y: int = 0
    node: int = 1
    children: list["PresetNode"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "node": self.node,
            "template": self.template,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PresetNode":
        return cls(
            identifier=d.get("identifier", ""),
            name=d.get("name", ""),
            template=d.get("template", ""),
            x=int(d.get("x") or 0),
            y=int(d.get("y") or 0),
            node=int(d.get("node") or 1),
            children=[cls.from_dict(c) for c in d.get("children", [])],
        )

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)

    def containers(self) -> int:
        return (1 if self.children else 0) + sum(c.containers() for c in self.children)


@dataclass
class Preset:
    id: str
    name: str
    created: str
    source: str
    roots: list[PresetNode]

    @property
    def kinds(self) -> int:
        return len(self.roots)

    @property
    def total(self) -> int:
        return sum(r.count() for r in self.roots)

    @property
    def containers(self) -> int:
        return sum(r.containers() for r in self.roots)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "source": self.source,
            "roots": [r.to_dict() for r in self.roots],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        roots = [PresetNode.from_dict(r) for r in d.get("roots", [])]
        return cls(
            id=d.get("id") or str(time.time()),
            name=d.get("name", "未命名模板"),
            created=d.get("created", ""),
            source=d.get("source", ""),
            roots=roots,
        )


class PresetStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path(__file__).with_name(PRESET_FILE)
        self.presets: list[Preset] = []
        self.load()

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("version", 1) < VERSION:
            return  # older flat format: re-capture instead of guessing
        for raw in data.get("presets", []):
            try:
                preset = Preset.from_dict(raw)
            except Exception:
                continue
            if preset.roots:
                self.presets.append(preset)

    def save(self) -> None:
        payload = {
            "version": VERSION,
            "note": "快速添加模板：把某个背包连同里面的箱子、箱子里的东西整棵树存下来，可套用到任意存档。",
            "presets": [p.to_dict() for p in self.presets],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- capture -------------------------------------------------------------

    def capture(self, editor, inv_key: str, name: str) -> Preset:
        """Freeze one bag (with full nesting) into a new preset."""
        doc = editor.inventory(inv_key)
        items_node = doc.root.get("saveItems")
        items = {}
        for index, node in enumerate(items_node.items):
            if index == 0:
                continue
            uuid_node = node.get("uuid", default=None)
            uuid = int(uuid_node.value) if uuid_node is not None else index
            items[uuid] = node

        def build(uuid: int, node_id: int) -> PresetNode:
            node = items[uuid]
            shape = node.get("itemModifiedShape", default=None) or node.get("itemShape", default=None)
            x = inventory._int(shape, "<minX>k__BackingField") if shape is not None else 0
            y = inventory._int(shape, "<minY>k__BackingField") if shape is not None else 0
            entry = PresetNode(
                identifier=inventory._str(node, "identifier"),
                name=inventory._str(node, "name"),
                template=doc.slice(node),
                x=x,
                y=y,
                node=node_id,
            )
            child_uuids = inventory._child_list(node)
            child_nodes = inventory._child_node_list(node)
            for index, child_uuid in enumerate(child_uuids):
                if child_uuid not in items:
                    continue
                node_for_child = child_nodes[index] if index < len(child_nodes) else 1
                entry.children.append(build(child_uuid, node_for_child))
            return entry

        # the bag's own direct children are the roots of the capture
        root_node = items_node.items[0]
        child_uuids = inventory._child_list(root_node)
        child_nodes = inventory._child_node_list(root_node)
        roots = []
        for index, uuid in enumerate(child_uuids):
            if uuid not in items:
                continue
            node_id = child_nodes[index] if index < len(child_nodes) else 1
            roots.append(build(uuid, node_id))

        label = dict((k, l) for k, l, _c in editor.inventories()).get(inv_key, inv_key)
        preset = Preset(
            id=f"{int(time.time())}",
            name=name,
            created=time.strftime("%Y-%m-%d %H:%M"),
            source=label,
            roots=roots,
        )
        self.presets.insert(0, preset)
        self.save()
        return preset

    # -- queries -------------------------------------------------------------

    def get(self, preset_id: str) -> Preset | None:
        for preset in self.presets:
            if preset.id == preset_id:
                return preset
        return None

    def remove(self, preset_id: str) -> bool:
        before = len(self.presets)
        self.presets = [p for p in self.presets if p.id != preset_id]
        if len(self.presets) != before:
            self.save()
            return True
        return False
