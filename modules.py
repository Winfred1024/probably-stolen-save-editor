"""
Custom modules ("模组") factory.

A module item is a normal item carrying:

    MODULE_TAG + MODULE_TYPE_<X>      the module's slot type
    ITEM_TYPE_STRING / MODULE_TYPE    both hold "MODULE_TYPE_<X>" as a string
    BONUS_PERCENTAGE_PERFORMANCE_INT  ┐
    BONUS_PERCENTAGE_EFFICIENCY_INT   ├ the "词条" (affixes), in percent
    BONUS_PERCENTAGE_QUALITY_INT      ┘
    TEMP_PERCENTAGE_*_INT             the game mirrors the same numbers here
    MODULE_TIER_INT                   module tier
    ADDITIONAL_EFFECT_STRING          free-form extra effect line
    itemShape / itemModifiedShape     width, height and a cell mask

Building a custom module therefore means cloning a real module (so sprite,
tags and structure are the game's own) and rewriting those fields, including
swapping the MODULE_TYPE_<X> tag when the type changes.
"""

from __future__ import annotations

import es3json

MODULE_TYPES = [
    ("MODULE_TYPE_UNIVERSAL", "通用", "通用模组"),
    ("MODULE_TYPE_EVAPORATOR", "蒸发器", "蒸发器模组"),
    ("MODULE_TYPE_CHEM", "化学设备", "化学设备模组"),
    ("MODULE_TYPE_ALARM", "警报", "警报模组"),
]

EFFECT_FIELDS = [
    ("BONUS_PERCENTAGE_PERFORMANCE_INT", "性能"),
    ("BONUS_PERCENTAGE_EFFICIENCY_INT", "效率"),
    ("BONUS_PERCENTAGE_QUALITY_INT", "品质"),
]

TEMP_PREFIX = "TEMP_PERCENTAGE_"
TIER_TAG = "MODULE_TIER_INT"
EXTRA_TAG = "ADDITIONAL_EFFECT_STRING"
TYPE_PREFIX = "MODULE_TYPE_"

MAX_SIDE = 6


def _text(node, key: str, default: str = "") -> str:
    child = node.get(key, default=None)
    return default if child is None or child.kind != "string" else child.value


def _int(node, key: str, default: int = 0) -> int:
    child = node.get(key, default=None)
    return default if child is None or child.kind != "number" else int(child.value)


def tag_map(node) -> dict[str, int]:
    """identifier -> index into _values."""
    values = node.get("_values", default=None)
    if values is None or values.kind != "array":
        return {}
    out = {}
    for i, entry in enumerate(values.items):
        name = _text(entry, "<identifier>k__BackingField")
        if name:
            out[name] = i
    return out


def is_module(node) -> bool:
    return "MODULE_TAG" in tag_map(node)


def module_type(node) -> str:
    for name in tag_map(node):
        if name.startswith(TYPE_PREFIX) and name != "MODULE_TYPE":
            return name
    return ""


def type_label(type_tag: str) -> str:
    for tag, label, _default_name in MODULE_TYPES:
        if tag == type_tag:
            return label
    return type_tag.replace(TYPE_PREFIX, "") or "未知"


def read_module(node) -> dict:
    """Everything the workshop shows for an existing module."""
    tags = tag_map(node)
    values = node.get("_values")
    shape = node.get("itemShape")

    effects = {}
    for tag, _label in EFFECT_FIELDS:
        if tag in tags:
            effects[tag] = _int(values.items[tags[tag]], "valueInt")

    width = _int(shape, "<width>k__BackingField", 1) if shape is not None else 1
    height = _int(shape, "<height>k__BackingField", 1) if shape is not None else 1
    mask = []
    data = shape.get("data", default=None) if shape is not None else None
    if data is not None and data.kind == "array":
        mask = [int(n.value) for n in data.items]

    return {
        "name": _text(node, "name"),
        "identifier": _text(node, "identifier"),
        "value": _int(node, "unitValue"),
        "tier": _int(values.items[tags[TIER_TAG]], "valueInt") if TIER_TAG in tags else 1,
        "type": module_type(node),
        "effects": effects,
        "extra": _text(values.items[tags[EXTRA_TAG]], "internalValueString") if EXTRA_TAG in tags else "",
        "width": width,
        "height": height,
        "mask": mask,
    }


def build_module(template_text: str, spec: dict) -> str:
    """Clone `template_text` and apply `spec` (name/value/tier/type/effects/mask)."""
    doc = es3json.Document(template_text)
    root = doc.root
    values = root.get("_values")
    keys = root.get("_keys")
    if values is None or keys is None:
        raise ValueError("这个模板不是模组物品（缺少 _values/_keys）")

    # -- basic fields
    if "name" in spec and root.get("name", default=None) is not None:
        doc.set(root.get("name"), str(spec["name"]))
    if "value" in spec and root.get("unitValue", default=None) is not None:
        doc.set(root.get("unitValue"), int(spec["value"]))

    tags = tag_map(root)

    # -- tier
    if TIER_TAG in tags:
        doc.set(values.items[tags[TIER_TAG]].get("valueInt"), int(spec.get("tier", 1)))

    # -- affixes (both the BONUS_ and the TEMP_ mirrors)
    for tag, _label in EFFECT_FIELDS:
        if tag not in tags or tag not in spec.get("effects", {}):
            continue
        amount = int(spec["effects"][tag])
        doc.set(values.items[tags[tag]].get("valueInt"), amount)
        mirror = TEMP_PREFIX + tag[len("BONUS_PERCENTAGE_"):]
        if mirror in tags:
            node = values.items[tags[mirror]].get("valueInt", default=None)
            if node is not None:
                doc.set(node, amount)

    # -- extra effect text
    if EXTRA_TAG in tags and "extra" in spec:
        node = values.items[tags[EXTRA_TAG]].get("internalValueString", default=None)
        if node is not None:
            doc.set(node, str(spec["extra"]))

    # -- module type (swap the MODULE_TYPE_<X> tag itself)
    new_type = spec.get("type")
    if new_type:
        old_type = module_type(root)
        if old_type and old_type != new_type:
            index = tags[old_type]
            entry = values.items[index]
            for field in ("<identifier>k__BackingField", "<identifierName>k__BackingField"):
                child = entry.get(field, default=None)
                if child is None:
                    continue
                value = child.value
                doc.set(child, value.replace(old_type, new_type) if old_type in value else new_type)
            doc.set(keys.items[index], new_type)
        for string_tag in ("ITEM_TYPE_STRING", "MODULE_TYPE"):
            if string_tag not in tags:
                continue
            node = values.items[tags[string_tag]].get("internalValueString", default=None)
            if node is not None:
                doc.set(node, new_type)

    # -- shape: width / height / mask, on both the base and the placed shape
    width = max(1, min(MAX_SIDE, int(spec.get("width", 1))))
    height = max(1, min(MAX_SIDE, int(spec.get("height", 1))))
    mask = list(spec.get("mask") or [1] * (width * height))[: width * height]
    if len(mask) < width * height:
        mask += [0] * (width * height - len(mask))
    if not any(mask):
        mask[0] = 1

    for shape_name in ("itemShape", "itemModifiedShape"):
        shape = root.get(shape_name, default=None)
        if shape is None:
            continue
        for field, value in (
            ("<width>k__BackingField", width),
            ("<height>k__BackingField", height),
        ):
            node = shape.get(field, default=None)
            if node is not None:
                doc.set(node, value)
        data = shape.get("data", default=None)
        if data is not None and data.kind == "array":
            doc.set_any(data, [int(c) for c in mask])

    return doc.render()


def make_mask(width: int, height: int, filled: bool = True) -> list[int]:
    return [1 if filled else 0] * (width * height)
