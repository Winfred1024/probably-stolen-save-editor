"""
Seed DNA: reading and rewriting a seed's genome.

A seed is a normal item whose `_values` carry its "DNA":

    PLANT_ID_STRING            which plant it grows into
    REQUIRED_PROGRESS          growth time (progress ticks)
    HARVEST_QUANTITY           yield per harvest
    HARVEST_ITEM_ID            what it produces
    REQUIRED_NUTRIMENT_LEVEL   nutrient level it wants
    REQUIRED_NUTRIMENT_RANGE   how far off it may drift
    NUTRIMENT_USE_RATE         nutrient drain
    WATER_USE_RATE             water drain
    CONTAMINANT_EFFECT_MULT    how much contamination hurts it
    GENETIC_HEAT_INT           genetic heat left over from splicing

...plus one `PLANT_TRAIT_<name>` tag per expressed gene.  A trait's `valueInt`
is its level; the tag only exists when the gene is expressed, so adding or
removing a trait means appending or deleting a tag entry.
"""

from __future__ import annotations

import es3json

SEED_TAG = "SEED_ITEM_TAG"
TRAIT_PREFIX = "PLANT_TRAIT_"
MAX_LEVEL = 10

# name -> (chinese label, what it does)
TRAITS: dict[str, tuple[str, str]] = {
    "simple_genome": ("简单基因组", "杂交更容易"),
    "complex_genome": ("复杂基因组", "性状多但难养"),
    "edible": ("可食用", "产物可以吃"),
    "unappetizing": ("难吃", "卖价偏低"),
    "nutritious": ("营养丰富", "产物营养更高"),
    "infertile": ("不育", "不能留种"),
    "hardy": ("耐逆", "耐恶劣环境"),
    "frail": ("脆弱", "必须养得好"),
    "bountiful": ("高产", "产量更多"),
    "fast_grower": ("速生", "长得更快"),
    "slow_grower": ("慢生", "长得更慢"),
    "thirsty": ("耗水", "喝水更多"),
    "nutrient_poor": ("耐贫瘠", "少吃养分"),
    "dense": ("致密", "产物更重更值钱"),
    "purifying": ("净化", "净化水质"),
    "potent": ("强效", "成分更浓"),
    "production_caffeine": ("产咖啡因", "生长时产出咖啡因"),
    "production_coffee_mush": ("产咖啡菇", "生长时产出咖啡菇"),
    "production_dream_cap_mush": ("产梦菇", "生长时产出梦菇"),
    "fertile": ("可育", "能留种，产量更高"),
    "perennial": ("多年生", "收完还能再长"),
    "weak": ("虚弱", "抗性差、产量低"),
    "sparse": ("稀疏", "产量少"),
    "delicious": ("美味", "更受欢迎、卖得上价"),
    "drought_resistant": ("耐旱", "喝水更少"),
    "polluting": ("污染", "生长时污染水质"),
}

STAT_FIELDS = [
    ("PLANT_ID_STRING", "植物 ID", "str"),
    ("HARVEST_ITEM_ID", "收获物", "str"),
    ("REQUIRED_PROGRESS", "生长时间", "int"),
    ("HARVEST_QUANTITY", "产量", "int"),
    ("REQUIRED_NUTRIMENT_LEVEL_TAG", "养分需求", "int"),
    ("REQUIRED_NUTRIMENT_RANGE_TAG", "养分耐受", "int"),
    ("NUTRIMENT_USE_RATE", "养分消耗", "int"),
    ("WATER_USE_RATE", "水分消耗", "int"),
    ("CONTAMINANT_EFFECT_MULT", "污染影响", "int"),
    ("GENETIC_HEAT_INT", "基因热度", "int"),
    ("PLANT_HEALTH_INT", "植株生命", "int"),
    ("GROWING_PROGRESS", "生长进度", "int"),
    ("ASSOCIATED_HYDROPONIC_ID", "水培槽编号", "int"),
    ("manufacturer", "厂商", "str"),
]

# tags whose valueInt mirrors another tag and should follow it
MIRRORS = {"PROGRESS_ITEM_TARGET_TAG": "REQUIRED_PROGRESS"}


def _text(node, key: str, default: str = "") -> str:
    child = node.get(key, default=None)
    return default if child is None or child.kind != "string" else child.value


def _int(node, key: str, default: int = 0) -> int:
    child = node.get(key, default=None)
    return default if child is None or child.kind != "number" else int(child.value)


def tag_index(node) -> dict[str, int]:
    values = node.get("_values", default=None)
    if values is None or values.kind != "array":
        return {}
    out = {}
    for i, entry in enumerate(values.items):
        name = _text(entry, "<identifier>k__BackingField")
        if name:
            out[name] = i
    return out


def is_seed(node) -> bool:
    return SEED_TAG in tag_index(node)


def read_seed(node) -> dict:
    """Decode a seed into stats + expressed traits."""
    index = tag_index(node)
    values = node.get("_values")
    stats = {}
    for tag, label, kind in STAT_FIELDS:
        if tag not in index:
            stats[tag] = None
            continue
        entry = values.items[index[tag]]
        stats[tag] = _text(entry, "internalValueString") if kind == "str" else _int(entry, "valueInt")

    traits = []
    for name in TRAITS:
        full = TRAIT_PREFIX + name
        if full in index:
            entry = values.items[index[full]]
            traits.append(
                {
                    "key": name,
                    "tag": full,
                    "label": TRAITS[name][0],
                    "hint": TRAITS[name][1],
                    "level": _int(entry, "valueInt"),
                    "enabled": bool(entry.get("valueEnabled").value)
                    if entry.get("valueEnabled", default=None) is not None
                    else True,
                }
            )
    return {"stats": stats, "traits": traits}


def summary(node) -> str:
    """One-line DNA readout for the item list."""
    info = read_seed(node)
    traits = info["traits"]
    if not traits:
        return ""
    parts = [f"{t['label']}·{t['level']}" for t in sorted(traits, key=lambda t: -t["level"])[:3]]
    return f"{info['stats'].get('HARVEST_ITEM_ID') or '?'} ← " + " / ".join(parts)


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #


def _set(entry_doc, field: str, value) -> None:
    child = entry_doc.root.get(field, default=None)
    if child is not None:
        entry_doc.set(child, value)


def apply_dna(doc: es3json.Document, node, stats: dict, levels: dict[str, int],
              tag_edits=None) -> None:
    """Write stats and the full trait set (0 = gene absent) back into doc.

    Every change inside _values is applied to a per-entry copy of its source
    text and the whole array is replaced once -- editing inside the array and
    replacing the array at the same time would splice overlapping spans.

    `tag_edits` (from the generic tag tab) is applied last, addressed by tag
    name so that adding/removing traits above cannot shift the wrong row.
    """
    values = node.get("_values")
    keys = node.get("_keys")
    if values is None or keys is None:
        raise ValueError("这个物品没有 _values/_keys，不是种子")

    texts = [doc.slice(e) for e in values.items]
    names = [_text(e, "<identifier>k__BackingField") for e in values.items]
    orig_names = list(names)
    index = {n: i for i, n in enumerate(names) if n}

    def edit(i: int, fn) -> None:
        entry = es3json.Document(texts[i])
        fn(entry)
        texts[i] = entry.render()

    # shape used when a tag has to be created from scratch
    template_text = None
    for i, name in enumerate(names):
        if name.startswith(TRAIT_PREFIX):
            template_text = texts[i]
            break
    if template_text is None and texts:
        template_text = texts[0]

    def add_tag(full: str, field: str, value) -> None:
        if template_text is None:
            raise ValueError("这个物品没有可复用的标签格式，无法新增字段")
        entry = es3json.Document(template_text)
        _set(entry, "<identifier>k__BackingField", full)
        _set(entry, "<identifierName>k__BackingField", "TYPE-STRING_" + full)
        _set(entry, "valueEnabled", True)
        _set(entry, "internalValueString", "")
        _set(entry, "valueInt", 0)
        _set(entry, field, value)
        texts.append(entry.render())
        names.append(full)

    # -- stats (patch existing, create the ones this seed is missing)
    for tag, _label, kind in STAT_FIELDS:
        if tag not in stats or stats[tag] is None or stats[tag] == "":
            continue
        field = "internalValueString" if kind == "str" else "valueInt"
        value = str(stats[tag]) if kind == "str" else int(stats[tag])
        if tag in index:
            def make(value=value, field=field):
                return lambda entry: _set(entry, field, value)

            edit(index[tag], make())
        else:
            add_tag(tag, field, value)
            index[tag] = len(texts) - 1

    # -- mirrors (PROGRESS_ITEM_TARGET_TAG follows REQUIRED_PROGRESS)
    for mirror, source in MIRRORS.items():
        if mirror in index and source in stats and stats[source] is not None:
            value = int(stats[source])

            def make(value=value):
                return lambda entry: _set(entry, "valueInt", value)

            edit(index[mirror], make())

    # -- traits: patch existing, append missing, delete level-0 ones
    drop = set()
    for name in TRAITS:
        full = TRAIT_PREFIX + name
        level = int(levels.get(name, 0) or 0)
        if full in index:
            i = index[full]
            if level <= 0:
                drop.add(i)
                continue
            edit(i, lambda entry, level=level: (
                _set(entry, "valueInt", level), _set(entry, "valueEnabled", True)
            ))
        elif level > 0:
            add_tag(full, "valueInt", level)
            index[full] = len(texts) - 1

    if drop:
        keep = [i for i in range(len(texts)) if i not in drop]
        texts = [texts[i] for i in keep]
        names = [names[i] for i in keep]

    # generic tag edits (附加标签 tab) come last and win over the DNA widgets
    if tag_edits:
        named = {}
        for position, changes in tag_edits.items():
            if 0 <= position < len(orig_names):
                named[orig_names[position]] = changes
        for name, changes in named.items():
            if name not in names:
                continue
            position = names.index(name)

            def apply_changes(entry, changes=changes):
                for field, value in changes.items():
                    if field.startswith("<"):
                        continue
                    _set(entry, field, value)

            edit(position, apply_changes)

    doc.set_text(values, "[" + ",".join(texts) + "]")
    doc.set_any(keys, names)
