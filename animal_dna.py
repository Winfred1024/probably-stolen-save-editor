"""
牲畜（老鼠）的遗传：双等位基因，表达值取平均。

游戏里 `GeneticHelper.GetExpressedValue` 就是：

    expressed = (alleleA + alleleB) / 2

繁殖时 `ApplyInheritedGenetics` 每条基因从父方随机拿一个等位基因、从母方
（物品上的 `_PARTNER_ALLELE_A/_B`）随机拿一个，各自再走一次
`MutateAllele`（15% 概率变异），最后重新取平均写回表达标签。

所以编辑时只要改 A/B 两个等位基因，表达值按同样的公式同步即可。
"""

from __future__ import annotations

import es3json

ANIMAL_TAG = "ANIMAL_ITEM_TAG"
GENE_SUFFIX_A = "_ALLELE_A"
GENE_SUFFIX_B = "_ALLELE_B"

# gene id -> (label, species base value, unit hint)
GENES: dict[str, tuple[str, float, str]] = {
    "ANIMAL_GROWTH_RATE_TAG": ("生长速度", 1.0, "倍"),
    "ANIMAL_HUNGER_RATE_TAG": ("饥饿速度", 65.0, ""),
    "ANIMAL_FERTILITY_TAG": ("生育力", 50.0, ""),
    "ANIMAL_LITTER_SIZE_TAG": ("每胎数量", 3.0, "只"),
    "ANIMAL_IMMUNITY_TAG": ("免疫力", 30.0, ""),
    "ANIMAL_MAX_HEALTH_TAG": ("最大生命", 100.0, ""),
    "ANIMAL_LONGEVITY_TAG": ("寿命倍率", 1.0, "倍"),
}

# non-genetic fields (tag -> label, kind)
STAT_FIELDS = [
    ("ANIMAL_SEXE_TAG", "性别", "str"),
    ("ANIMAL_AGE_TAG", "年龄（天）", "int"),
    ("ANIMAL_WEIGHT_TAG", "体重", "float"),
    ("ANIMAL_HEALTH_TAG", "当前生命", "float"),
    ("ANIMAL_HUNGER_TAG", "当前饱食", "float"),
    ("ANIMAL_MAX_HUNGER_TAG", "饱食上限", "float"),
    ("ANIMAL_CURRENT_FERTILITY_TAG", "当前生育力", "float"),
    ("ANIMAL_GESTATION_PERIOD_TAG", "妊娠期（天）", "float"),
    ("ANIMAL_LITTER_SIZE_VARIANCE_TAG", "每胎浮动", "float"),
    ("ANIMAL_MAX_MATING_COUNT_TAG", "最多交配次数", "float"),
]

PARTNER_KEYS = ("_PARTNER_ALLELE_A", "_PARTNER_ALLELE_B")


def _text(node, key: str, default: str = "") -> str:
    child = node.get(key, default=None)
    return default if child is None or child.kind != "string" else child.value


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


def is_animal(node) -> bool:
    return ANIMAL_TAG in tag_index(node)


def _float(node, key: str, default: float = 0.0) -> float:
    values = node.get("_values", default=None)
    index = tag_index(node)
    if key not in index:
        return default
    entry = values.items[index[key]]
    child = entry.get("valueFloat", default=None)
    try:
        return float(child.value) if child is not None else default
    except (TypeError, ValueError):
        return default


def _int(node, key: str, default: int = 0) -> int:
    index = tag_index(node)
    if key not in index:
        return default
    child = node.get("_values").items[index[key]].get("valueInt", default=None)
    try:
        return int(child.value) if child is not None else default
    except (TypeError, ValueError):
        return default


def read_animal(node) -> dict:
    index = tag_index(node)
    genes = []
    for gene, (label, base, unit) in GENES.items():
        if (gene + GENE_SUFFIX_A) not in index:
            continue
        a = _float(node, gene + GENE_SUFFIX_A, base)
        b = _float(node, gene + GENE_SUFFIX_B, base)
        expressed = _float(node, gene, (a + b) / 2)
        genes.append(
            {
                "id": gene,
                "label": label,
                "unit": unit,
                "a": a,
                "b": b,
                "expressed": expressed,
                "base": base,
                "partner_a": _float(node, gene + PARTNER_KEYS[0], 0.0),
                "partner_b": _float(node, gene + PARTNER_KEYS[1], 0.0),
            }
        )

    stats = {}
    kinds = {}
    for tag, _label, kind in STAT_FIELDS:
        kinds[tag] = kind
        if tag not in index:
            stats[tag] = None
        elif kind == "str":
            values = node.get("_values")
            stats[tag] = _text(values.items[index[tag]], "internalValueString")
        elif kind == "int":
            stats[tag] = _int(node, tag)
        else:
            stats[tag] = _float(node, tag)
    species = ""
    if "ANIMAL_SPECIES_TAG" in index:
        values = node.get("_values")
        species = _text(values.items[index["ANIMAL_SPECIES_TAG"]], "internalValueString")
    return {"genes": genes, "stats": stats, "kinds": kinds, "species": species}


def summary(node) -> str:
    info = read_animal(node)
    if not info["genes"]:
        return ""
    sex = "公" if str(info["stats"].get("ANIMAL_SEXE_TAG", "")).upper().startswith("M") else "母"
    age = info["stats"].get("ANIMAL_AGE_TAG")
    fertility = next(
        (g["expressed"] for g in info["genes"] if g["id"] == "ANIMAL_FERTILITY_TAG"), None
    )
    bits = [sex]
    if age is not None:
        bits.append(f"{age}天")
    if fertility is not None:
        bits.append(f"生育 {fertility:.0f}")
    return " · ".join(bits)


def _set_float(entry_doc, value: float) -> None:
    child = entry_doc.root.get("valueFloat", default=None)
    if child is not None:
        entry_doc.set(child, float(value))


def apply_animal(doc: es3json.Document, node, genes: dict, stats: dict) -> None:
    """Write alleles (and their averaged expression) plus the plain stats."""
    values = node.get("_values")
    keys = node.get("_keys")
    if values is None or keys is None:
        raise ValueError("这个物品没有 _values/_keys，不是牲畜")

    texts = [doc.slice(e) for e in values.items]
    names = [_text(e, "<identifier>k__BackingField") for e in values.items]
    index = {n: i for i, n in enumerate(names) if n}

    def edit(position: int, fn) -> None:
        entry = es3json.Document(texts[position])
        fn(entry)
        texts[position] = entry.render()

    for gene, spec in genes.items():
        a = float(spec.get("a", 0.0))
        b = float(spec.get("b", 0.0))
        expressed = (a + b) / 2
        for key, value in ((gene + GENE_SUFFIX_A, a), (gene + GENE_SUFFIX_B, b), (gene, expressed)):
            if key not in index:
                continue
            edit(index[key], lambda entry, value=value: _set_float(entry, value))
        if spec.get("partner_a") is not None and (gene + PARTNER_KEYS[0]) in index:
            edit(index[gene + PARTNER_KEYS[0]],
                 lambda entry, value=float(spec["partner_a"]): _set_float(entry, value))
        if spec.get("partner_b") is not None and (gene + PARTNER_KEYS[1]) in index:
            edit(index[gene + PARTNER_KEYS[1]],
                 lambda entry, value=float(spec["partner_b"]): _set_float(entry, value))

    for tag, _label, kind in STAT_FIELDS:
        if tag not in index or tag not in stats or stats[tag] is None:
            continue
        value = stats[tag]
        if kind == "str":
            def set_str(entry, value=value):
                child = entry.root.get("internalValueString", default=None)
                if child is not None:
                    entry.set(child, str(value))
            edit(index[tag], set_str)
        elif kind == "int":
            def set_int(entry, value=value):
                child = entry.root.get("valueInt", default=None)
                if child is not None:
                    entry.set(child, int(value))
            edit(index[tag], set_int)
        else:
            edit(index[tag], lambda entry, value=value: _set_float(entry, float(value)))

    doc.set_text(values, "[" + ",".join(t.strip() for t in texts) + "]")
    doc.set_any(keys, names)
