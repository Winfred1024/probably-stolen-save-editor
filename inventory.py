"""
Grid-inventory surgery for "Probably Stolen" saves.

An inventory is stored (inside the save) as a JSON string whose only key is
`saveItems`: one flat array of item elements.  The tree is expressed with two
parallel arrays on every element:

    childItems                uuids of the items sitting inside this container
    childItemInventoryNode    which container node (tab) each child lives in

Observed invariants across every inventory in the game's own saves:

    * uuids are dense and equal to the element's array index
    * exactly one element has uuid 0 (the bag itself, the root container)
    * every other element is referenced exactly once by some parent

Adding or deleting an element therefore means renumbering, which is why both
operations rebuild the array from the original *source text* of each element:
untouched items keep their exact bytes (including float formatting) and only the
uuids / child lists that really change are re-serialised.
"""

from __future__ import annotations

from dataclasses import dataclass

import es3json

SHAPE_FIELDS = ("<minX>k__BackingField", "<minY>k__BackingField",
                "<width>k__BackingField", "<height>k__BackingField")


@dataclass
class Item:
    index: int
    node: es3json.Node
    uuid: int
    identifier: str
    name: str
    types: list[str]
    unique_id: int
    width: int
    height: int
    x: int
    y: int

    @property
    def footprint(self) -> str:
        if self.width == 1 and self.height == 1:
            return "1×1"
        return f"{self.width}×{self.height}"


class InventoryError(RuntimeError):
    pass


class Inventory:
    """Editable view over one `*InvJSON` document."""

    def __init__(self, doc: es3json.Document):
        self.doc = doc
        self.items_node = doc.root.get("saveItems")
        if self.items_node.kind != "array":
            raise InventoryError("存档里的 saveItems 不是数组")

    # -- reading -------------------------------------------------------------

    def items(self) -> list[Item]:
        out = []
        for index, node in enumerate(self.items_node.items):
            shape = node.get("itemModifiedShape", default=None)
            base = node.get("itemShape", default=None)
            ref = shape if shape is not None else base
            out.append(
                Item(
                    index=index,
                    node=node,
                    uuid=_int(node, "uuid"),
                    identifier=_str(node, "identifier"),
                    name=_str(node, "name"),
                    types=[t.value for t in node.get("itemTypes").items]
                    if node.get("itemTypes", default=None) is not None
                    else [],
                    unique_id=_int(node, "uniqueId"),
                    width=_int(ref, "<width>k__BackingField") if ref is not None else 1,
                    height=_int(ref, "<height>k__BackingField") if ref is not None else 1,
                    x=_int(ref, "<minX>k__BackingField") if ref is not None else 0,
                    y=_int(ref, "<minY>k__BackingField") if ref is not None else 0,
                )
            )
        return out

    def container_items(self, parent_uuid: int = 0) -> list[Item]:
        """Items directly inside a container."""
        items = self.items()
        if not items:
            return []
        children = set(_child_list(items[parent_uuid].node))
        return [it for it in items if it.uuid in children and it.uuid != parent_uuid]

    def parent_of(self, uuid: int) -> int | None:
        for it in self.items():
            if uuid in set(_child_list(it.node)):
                return it.uuid
        return None

    def next_unique_id(self) -> int:
        return max((it.unique_id for it in self.items()), default=0) + 1

    # -- placement -----------------------------------------------------------

    # The main bag's grid: every save observed so far has items filling a
    # 24 x 10 area, and anything placed past that column simply never shows up
    # in game (it is outside the visible grid).  So placement is clamped to at
    # least this area, and refuses to invent cells beyond what the bag shows.
    GRID_FLOOR_W = 24
    GRID_FLOOR_H = 10

    def grid_size(self, parent_uuid: int) -> tuple[int, int]:
        width = height = 0
        for it in self.container_items(parent_uuid):
            width = max(width, it.x + max(1, it.width))
            height = max(height, it.y + max(1, it.height))
        return max(self.GRID_FLOOR_W, width), max(self.GRID_FLOOR_H, height)

    def occupied_cells(self, parent_uuid: int) -> set[tuple[int, int]]:
        occupied = set()
        for it in self.container_items(parent_uuid):
            for yy in range(it.y, it.y + max(1, it.height)):
                for xx in range(it.x, it.x + max(1, it.width)):
                    occupied.add((xx, yy))
        return occupied

    def spot_in(self, occupied: set, width: int, height: int,
                grid_w: int, grid_h: int) -> tuple[int, int] | None:
        """First free cell for a width×height item, or None when the grid is full."""
        width = max(1, width)
        height = max(1, height)
        if width > grid_w or height > grid_h:
            return None
        for y in range(grid_h - height + 1):
            for x in range(grid_w - width + 1):
                cells = [(x + dx, y + dy) for dy in range(height) for dx in range(width)]
                if not any(c in occupied for c in cells):
                    return x, y
        return None

    def free_spot(self, parent_uuid: int, width: int, height: int) -> tuple[int, int]:
        """First free cell for a width×height item, inside the bag's real grid."""
        occupied = self.occupied_cells(parent_uuid)
        grid_w, grid_h = self.grid_size(parent_uuid)
        width = max(1, width)
        height = max(1, height)
        if width > grid_w or height > grid_h:
            raise InventoryError(f"这件物品是 {width}×{height}，比背包格子（{grid_w}×{grid_h}）还大")
        spot = self.spot_in(occupied, width, height, grid_w, grid_h)
        if spot is not None:
            return spot
        raise InventoryError(
            f"背包里已经没有空位放这件 {width}×{height} 的东西了（可用 {grid_w}×{grid_h}）。"
            "先删掉或挪走一些物品再添加。"
        )

    # -- editing -------------------------------------------------------------

    def add_item(self, template_text: str, parent_uuid: int = 0, node_id: int = 1,
                 unique_id: int | None = None) -> int:
        """Append a clone of `template_text` inside `parent_uuid`; returns new index."""
        items = self.items()
        if not items:
            raise InventoryError("这个背包是空的，没有根容器")
        if parent_uuid >= len(items):
            raise InventoryError("找不到目标容器")

        new_uuid = len(items)
        width = _template_size(template_text)
        x, y = self.free_spot(parent_uuid, width[0], width[1])

        if unique_id is None:
            unique_id = self.next_unique_id()
        patched = _patch_template(template_text, new_uuid, unique_id, x, y)

        parent = items[parent_uuid]
        child_list = _child_list(parent.node)
        node_list = _child_node_list(parent.node)
        child_list.append(new_uuid)
        node_list.append(node_id)

        texts = [self.doc.slice(it.node) for it in items]
        texts[parent_uuid] = _rerender(
            texts[parent_uuid],
            [(("childItems",), child_list), (("childItemInventoryNode",), node_list)],
        )
        texts.append(patched)
        self.doc.set_text(self.items_node, "[" + ",".join(texts) + "]")
        return new_uuid

    def add_many(self, templates, parent_uuid: int = 0, node_id: int = 1,
                 unique_base: int | None = None, positions=None):
        """Append many clones in one pass (one document rewrite).

        Items are packed largest-first by the caller; anything that does not fit
        is returned in skipped instead of raising, so a big kit still lands.
        """
        items = self.items()
        if not items:
            raise InventoryError("这个背包是空的，没有根容器")
        if parent_uuid >= len(items):
            raise InventoryError("找不到目标容器")

        texts = [self.doc.slice(it.node) for it in items]
        parent = items[parent_uuid]
        child_list = _child_list(parent.node)
        node_list = _child_node_list(parent.node)
        occupied = self.occupied_cells(parent_uuid)
        grid_w, grid_h = self.grid_size(parent_uuid)

        next_uuid = len(items)
        uid = self.next_unique_id() if unique_base is None else int(unique_base)
        added: list[tuple[int, str]] = []
        skipped: list[str] = []

        for order, template in enumerate(templates):
            width, height = _template_size(template)
            if positions is not None:
                # replaying a captured layout: keep the original cell
                if order >= len(positions) or positions[order] is None:
                    skipped.append(template)
                    continue
                x, y = positions[order]
                for dy in range(max(1, height)):
                    for dx in range(max(1, width)):
                        occupied.add((x + dx, y + dy))
            else:
                spot = self.spot_in(occupied, width, height, grid_w, grid_h)
                if spot is None:
                    skipped.append(template)
                    continue
                x, y = spot
                for dy in range(max(1, height)):
                    for dx in range(max(1, width)):
                        occupied.add((x + dx, y + dy))
            texts.append(_patch_template(template, next_uuid, uid, x, y))
            child_list.append(next_uuid)
            node_list.append(node_id)
            added.append((next_uuid, _template_identifier(template)))
            next_uuid += 1
            uid += 1

        if added:
            texts[parent_uuid] = _rerender(
                texts[parent_uuid],
                [(("childItems",), child_list), (("childItemInventoryNode",), node_list)],
            )
            self.doc.set_text(self.items_node, "[" + ",".join(texts) + "]")
        return added, skipped

    def remove_item(self, index: int) -> None:
        """Delete one item and every reference to it, renumbering the rest."""
        items = self.items()
        if not 0 <= index < len(items):
            raise InventoryError("要删除的物品不存在")
        if index == 0:
            raise InventoryError("不能删除背包本身")

        texts: list[str] = []
        for old_uuid, it in enumerate(items):
            if old_uuid == index:
                continue
            new_uuid = old_uuid - 1 if old_uuid > index else old_uuid
            patches = []
            if new_uuid != old_uuid:
                patches.append((("uuid",), new_uuid))
            for field in ("childItems", "childItemInventoryNode"):
                if field == "childItems":
                    refs = _child_list(it.node)
                    remapped = [r - 1 if r > index else r for r in refs if r != index]
                else:
                    refs = _child_node_list(it.node)
                    keep = _keep_mask(_child_list(it.node), index)
                    if len(keep) != len(refs):
                        keep = keep[: len(refs)]
                    remapped = [r for r, alive in zip(refs, keep) if alive]
                if remapped != refs:
                    patches.append(((field,), remapped))
            text = self.doc.slice(it.node)
            if patches:
                text = _rerender(text, patches)
            texts.append(text)

        if not texts:
            raise InventoryError("删掉这一件之后背包就空了")
        self.doc.set_text(self.items_node, "[" + ",".join(texts) + "]")

    # -- verification --------------------------------------------------------

    def check(self) -> list[str]:
        """Re-check the invariants the game itself relies on."""
        items = self.items()
        problems = []
        if not items:
            return ["背包为空"]
        if items[0].uuid != 0:
            problems.append("第一个不是根容器")
        for i, it in enumerate(items):
            if it.uuid != i:
                problems.append(f"第 {i} 件 uuid={it.uuid} 不等于下标")
        referenced = {}
        for it in items:
            for child in _child_list(it.node):
                referenced[child] = referenced.get(child, 0) + 1
        for i, it in enumerate(items[1:], start=1):
            n = referenced.get(i, 0)
            if n != 1:
                problems.append(f"第 {i} 件被引用 {n} 次")
        return problems


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _int(node, key: str, default: int = 0) -> int:
    child = node.get(key, default=None)
    if child is None or child.kind != "number":
        return default
    return int(child.value)


def _str(node, key: str, default: str = "") -> str:
    child = node.get(key, default=None)
    if child is None or child.kind != "string":
        return default
    return child.value


def _child_list(node) -> list[int]:
    arr = node.get("childItems", default=None)
    if arr is None or arr.kind != "array":
        return []
    return [int(n.value) for n in arr.items]


def _child_node_list(node) -> list[int]:
    arr = node.get("childItemInventoryNode", default=None)
    if arr is None or arr.kind != "array":
        return []
    return [int(n.value) for n in arr.items]


def _keep_mask(refs: list[int], removed: int) -> list[bool]:
    return [r != removed for r in refs]


def _template_identifier(text: str) -> str:
    try:
        node = es3json.Document(text).root.get("identifier", default=None)
        return node.value if node is not None else ""
    except Exception:
        return ""


def _template_size(text: str) -> tuple[int, int]:
    doc = es3json.Document(text)
    shape = doc.root.get("itemModifiedShape", default=None) or doc.root.get("itemShape", default=None)
    if shape is None:
        return 1, 1
    return (
        max(1, _int(shape, "<width>k__BackingField", 1)),
        max(1, _int(shape, "<height>k__BackingField", 1)),
    )


def _patch_template(text: str, uuid: int, unique_id: int, x: int, y: int) -> str:
    """Rewrite the identity/position fields of a cloned item, keeping its bytes."""
    doc = es3json.Document(text)
    doc.set(doc.root.get("uuid"), uuid)
    if doc.root.get("uniqueId", default=None) is not None:
        doc.set(doc.root.get("uniqueId"), unique_id)

    # Only the *modified* shape carries a grid position.  itemShape is the
    # item's canonical footprint and always sits at (0,0) -- writing the
    # placement into it corrupts the item's own shape.
    shape = doc.root.get("itemModifiedShape", default=None)
    if shape is not None:
        if shape.get("<minX>k__BackingField", default=None) is not None:
            doc.set(shape.get("<minX>k__BackingField"), x)
        if shape.get("<minY>k__BackingField", default=None) is not None:
            doc.set(shape.get("<minY>k__BackingField"), y)
    base = doc.root.get("itemShape", default=None)
    if base is not None:
        for field_name in ("<minX>k__BackingField", "<minY>k__BackingField"):
            node = base.get(field_name, default=None)
            if node is not None and node.kind == "number" and node.value != 0:
                doc.set(node, 0)

    # a clone that is itself a container must not inherit the donor's children:
    # those uuids belong to a different save and would corrupt the tree
    for field in ("childItems", "childItemInventoryNode"):
        node = doc.root.get(field, default=None)
        if node is not None and node.kind == "array" and len(node):
            doc.set_any(node, [])
    return doc.render()


def _rerender(text: str, patches) -> str:
    doc = es3json.Document(text)
    for path, value in patches:
        node = doc.root
        for step in path:
            node = node[step]
        if node.kind == "array":
            doc.set_any(node, value)
        else:
            doc.set(node, value)
    return doc.render()
