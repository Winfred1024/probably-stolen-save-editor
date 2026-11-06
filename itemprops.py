"""
Item property schema + patching.

Every item element in a save holds the same shape:

    identifier, unitCount, uuid, itemType, name, shortDescription, flavorText,
    uniqueId, unitBaseValue, unitValue, lateUnitValue, backupUnitValue,
    spriteAtlasPath, spritePath,
    itemShape{...}, itemModifiedShape{...},
    itemTypes[], childItems[], childItemInventoryNode[],
    _keys[], _values[{identifier, identifierName, valueEnabled,
                      internalValueString, valueInt, valueFloat, valueLong,
                      valueDouble, valueBool}]

This module names every one of those fields, reads them out of a node and writes
patched values back through es3json's span editor so untouched bytes stay put.
"""

from __future__ import annotations

import es3json

# (key, label, kind)
BASIC_FIELDS = [
    ("identifier", "标识（内部 ID）", "str"),
    ("name", "名称", "str"),
    ("itemType", "类型类", "str"),
    ("unitCount", "堆叠数量", "int"),
    ("uniqueId", "物品编号", "int"),
    ("unitBaseValue", "基础估值", "int"),
    ("unitValue", "当前估值", "int"),
    ("lateUnitValue", "延迟估值", "int"),
    ("backupUnitValue", "备用估值", "int"),
    ("spriteAtlasPath", "贴图图集", "str"),
    ("spritePath", "贴图名", "str"),
]

TEXT_FIELDS = [
    ("shortDescription", "简介"),
    ("flavorText", "风味文本"),
]

SHAPE_FIELDS = [
    ("<minX>k__BackingField", "X", "int"),
    ("<minY>k__BackingField", "Y", "int"),
    ("<width>k__BackingField", "宽", "int"),
    ("<height>k__BackingField", "高", "int"),
    ("<orientation>k__BackingField", "朝向", "int"),
    ("<flipped>k__BackingField", "翻转", "bool"),
    ("outside", "外置", "int"),
]

SHAPES = [("itemShape", "基础形状"), ("itemModifiedShape", "当前形状（格子位置）")]

TAG_FIELDS = [
    ("valueEnabled", "启用", "bool"),
    ("internalValueString", "字符串", "str"),
    ("valueInt", "Int", "int"),
    ("valueFloat", "Float", "float"),
    ("valueLong", "Long", "int"),
    ("valueDouble", "Double", "float"),
    ("valueBool", "Bool", "bool"),
]

# --------------------------------------------------------------------------- #
# containers / contents
# --------------------------------------------------------------------------- #

PART_PREFIX = "CURRENT_PART_"

# every blendable item carries the full part table; amounts are microlitres
PART_LABELS = {
    "WATER": "水",
    "HEAVY_METAL": "重金属",
    "ORGANIC_WASTE": "有机废物",
    "MICROPLASTIC": "微塑料",
    "MICROBE": "微生物",
    "CHEMICAL_CONTAMINANT": "化学污染物",
    "PHYSICAL_CONTAMINANT": "物理杂质",
    "MINERAL": "矿物质",
    "DREAM_CAP_MUSH": "梦帽菇",
    "DREAM_CAP_EXTRACT": "梦帽菇提取物",
    "PLANT_MATTER": "植物纤维",
    "ALCOHOL": "酒精",
    "CAFFEINE": "咖啡因",
    "PROTEIN": "蛋白质",
    "FAT": "脂肪",
    "STARCH": "淀粉",
    "SUGAR": "糖",
    "COFFEE_MUSH": "咖啡菇",
    "CONTRACEPTIVE": "避孕成分",
    "ZYANIDE": "氰化物",
    "NIGHTMARE_EXTRACT": "梦魇提取物",
    "NIGHTMARE_CAP_MUSH": "梦魇帽菇",
    "CHLORINE": "氯",
    "SYNAPREST": "突触宁",
    "FANTA": "芬达",
    "METHYLOL": "羟甲基",
    "REDUCING_AGENT": "还原剂",
    "ACID": "酸",
    "BASE": "碱",
    "STABILIZING_AGENT": "稳定剂",
    "DEBRIS": "碎屑",
    "LIPOBOND_AGENT": "脂质黏合剂",
    "LIVESTOCK_STEROID": "牲畜类固醇",
    "LIVESTOCK_IMMUNITY": "牲畜免疫剂",
    "WINE_EXTRACT": "葡萄酒提取物",
    "NOMORE": "诺摩尔",
}

CAPACITY_TAG = "LIQUID_CONTAINER_CAPACITY"
TEMPERATURE_TAG = "CONTAINER_TEMP_TAG"
PURITY_TAG = "CHEMICAL_PRODUCT_PURITY_TAG"
PRICE_TAG = "LIQUID_CONTAINER_BASE_PRICE_TAG"

# used by the quick actions in the editor
WATER_PART = "WATER"


def _tag_index_map(node) -> dict[str, int]:
    values = node.get("_values", default=None)
    if values is None or values.kind != "array":
        return {}
    out = {}
    for i, entry in enumerate(values.items):
        name = _text(entry, "<identifier>k__BackingField")
        if name:
            out[name] = i
    return out


def container_info(node) -> dict | None:
    """Contents of a liquid/mixture container, or None for plain items."""
    index = _tag_index_map(node)
    part_keys = [k for k in index if k.startswith(PART_PREFIX)]
    if not part_keys and CAPACITY_TAG not in index:
        return None

    values_node = node.get("_values")
    parts = []
    for key in part_keys:
        entry = values_node.items[index[key]]
        amount = entry.get("valueInt", default=None)
        parts.append(
            {
                "key": key,
                "index": index[key],
                "label": PART_LABELS.get(key[len(PART_PREFIX):], key[len(PART_PREFIX):]),
                "amount": int(amount.value) if amount is not None else 0,
                "float": 0.0,
            }
        )
    parts.sort(key=lambda p: (-p["amount"], p["label"]))

    info = {
        "parts": parts,
        "total": sum(p["amount"] for p in parts),
        "capacity": None,
        "temperature": None,
        "purity": None,
        "price": None,
    }
    for tag, field, name in (
        (CAPACITY_TAG, "valueInt", "capacity"),
        (TEMPERATURE_TAG, "valueDouble", "temperature"),
        (PURITY_TAG, "valueDouble", "purity"),
        (PRICE_TAG, "valueInt", "price"),
    ):
        if tag not in index:
            continue
        entry = values_node.items[index[tag]]
        child = entry.get(field, default=None)
        if child is None:
            continue
        info[name] = {
            "index": index[tag],
            "value": float(child.value) if name in ("temperature", "purity") else int(child.value),
            "field": field,
        }
    return info


def _amount_to_text(amount: int) -> str:
    """Microlitres -> a readable volume."""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f} L"
    if amount >= 1000:
        return f"{amount / 1000:.1f} ml"
    return f"{amount} µl"


def _compact_volume(amount: float) -> str:
    """Short form for table cells: 750ml / 3.0L / 250µl."""
    if amount >= 1_000_000:
        text = f"{amount / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{text}L"
    if amount >= 1000:
        text = f"{amount / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{text}ml"
    return f"{int(amount)}µl"


def container_summary(node) -> str:
    """One-line contents description for list views ('' when not a container)."""
    info = container_info(node)
    if info is None:
        return ""
    capacity_item = info.get("capacity")
    capacity = int(capacity_item["value"]) if capacity_item else 0
    if not info["parts"] or not info["total"]:
        return f"空 / {_compact_volume(capacity)}" if capacity else "空"
    top = info["parts"][0]
    text = f"{top['label']} {_compact_volume(info['total'])}"
    if capacity:
        text += f" / {_compact_volume(capacity)}"
    return text



# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def read_basic(node) -> dict[str, object]:
    out = {}
    for key, _label, kind in BASIC_FIELDS:
        child = node.get(key, default=None)
        if child is None:
            out[key] = None
        elif kind == "bool":
            out[key] = bool(child.value)
        elif kind == "int":
            out[key] = int(child.value)
        else:
            out[key] = child.value
    return out


def read_texts(node) -> dict[str, str]:
    out = {}
    for key, _label in TEXT_FIELDS:
        child = node.get(key, default=None)
        out[key] = "" if child is None or child.kind != "string" else child.value
    return out


def read_shapes(node) -> dict[str, dict[str, object]]:
    out = {}
    for shape_key, _label in SHAPES:
        shape = node.get(shape_key, default=None)
        values = {}
        if shape is not None:
            for field, _flabel, kind in SHAPE_FIELDS:
                child = shape.get(field, default=None)
                if child is None:
                    continue
                values[field] = bool(child.value) if kind == "bool" else int(child.value)
        out[shape_key] = values
    return out


def read_types(node) -> list[str]:
    arr = node.get("itemTypes", default=None)
    if arr is None or arr.kind != "array":
        return []
    return [n.value for n in arr.items]


def read_tags(node) -> list[dict]:
    values = node.get("_values", default=None)
    if values is None or values.kind != "array":
        return []
    keys_node = node.get("_keys", default=None)
    keys = [n.value for n in keys_node.items] if keys_node is not None and keys_node.kind == "array" else []
    tags = []
    for i, entry in enumerate(values.items):
        tag = {
            "index": i,
            "identifier": _text(entry, "<identifier>k__BackingField"),
            "identifierName": _text(entry, "<identifierName>k__BackingField"),
            "key": keys[i] if i < len(keys) else "",
        }
        for field, _label, kind in TAG_FIELDS:
            child = entry.get(field, default=None)
            if child is None:
                continue
            if kind == "bool":
                tag[field] = bool(child.value)
            elif kind == "int":
                tag[field] = int(child.value)
            elif kind == "float":
                tag[field] = float(child.value)
            else:
                tag[field] = child.value
        tags.append(tag)
    return tags


def _text(node, key: str) -> str:
    child = node.get(key, default=None)
    return "" if child is None or child.kind != "string" else child.value


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #


def apply_edits(doc: es3json.Document, node, basic: dict, texts: dict, shapes: dict,
                types: list[str], tag_edits: dict[int, dict]) -> None:
    """Patch every changed field of one item into `doc` (span-level)."""
    for key, _label, kind in BASIC_FIELDS:
        value = basic.get(key)
        child = node.get(key, default=None)
        if value is None or child is None:
            continue
        if kind == "int":
            doc.set(child, int(value))
        elif kind == "bool":
            doc.set(child, bool(value))
        else:
            doc.set(child, str(value))

    for key, _label in TEXT_FIELDS:
        child = node.get(key, default=None)
        if child is not None and key in texts:
            doc.set(child, str(texts[key]))

    for shape_key, _label in SHAPES:
        shape = node.get(shape_key, default=None)
        values = shapes.get(shape_key) or {}
        if shape is None:
            continue
        for field, _flabel, kind in SHAPE_FIELDS:
            if field not in values:
                continue
            child = shape.get(field, default=None)
            if child is None:
                continue
            doc.set(child, bool(values[field]) if kind == "bool" else int(values[field]))
        # width/height and the cell mask must agree, or the item renders wrong
        width = int(values.get("<width>k__BackingField", 0) or 0)
        height = int(values.get("<height>k__BackingField", 0) or 0)
        data = shape.get("data", default=None)
        if width > 0 and height > 0 and data is not None and data.kind == "array" and len(data) != width * height:
            doc.set_any(data, [1] * (width * height))

    types_node = node.get("itemTypes", default=None)
    if types_node is not None and types_node.kind == "array":
        doc.set_any(types_node, [str(t) for t in types])

    values_node = node.get("_values", default=None)
    keys_node = node.get("_keys", default=None)
    if values_node is not None and tag_edits:
        for index, values in tag_edits.items():
            if not 0 <= index < len(values_node.items):
                continue
            entry = values_node.items[index]
            for field, _label, kind in TAG_FIELDS:
                if field not in values:
                    continue
                child = entry.get(field, default=None)
                if child is None:
                    continue
                if kind == "bool":
                    doc.set(child, bool(values[field]))
                elif kind == "int":
                    doc.set(child, int(values[field]))
                elif kind == "float":
                    doc.set(child, float(values[field]))
                else:
                    doc.set(child, str(values[field]))
            # keep _keys parallel with _values
            if keys_node is not None and keys_node.kind == "array" and index < len(keys_node.items):
                identifier = _text(entry, "<identifier>k__BackingField")
                if identifier and keys_node.items[index].value != identifier:
                    doc.set(keys_node.items[index], identifier)
