"""
初始人物特性（starting perks）。

存档里是 `playerStore.startingPerks`：每个元素是一条完整定义

    {"id": "saving", "cost": 1, "maxSlot": 0, "incompatiblePerks": [],
     "type": 0, "mindEditable": false, "rawName": null, "rawDescription": null}

`type` 0 = 正面、1 = 负面、2 = 特殊；`cost` 为负表示「白送点数」的缺陷。
定义本身从游戏的 `StartingPerkList` 里解包出来（`perk_catalog.json`）。
"""

from __future__ import annotations

import json
from pathlib import Path

CATALOG_FILE = "perk_catalog.json"

LABELS = {
    "saving": "储蓄",
    "bad_lease": "烂租约",
    "convict": "前科犯",
    "model_citizen": "模范市民",
    "well_connected": "人脉广",
    "sycophant": "谄媚",
    "awful_negotiator": "谈判糟糕",
    "public_enemy": "公敌",
    "alert": "警觉",
    "sawyer_crew": "索耶船员",
    "diplomatic": "圆滑",
    "intimidating": "凶悍",
    "side_street": "偏街店面",
    "nicotine_addiction": "尼古丁成瘾",
    "narcotic_addiction": "药物成瘾",
    "alcohol_addiction": "酒精成瘾",
    "secret_admirer": "秘密爱慕者",
    "colorful_character": "话题人物",
    "blackmail": "被勒索",
    "blabbermouth": "大嘴巴",
    "minimalist": "极简主义",
    "ascetic": "苦行者",
    "sentinel": "守望者",
    "agoraphobia": "广场恐惧",
    "welfare_program": "福利计划",
    "makeshift_reactor": "土制反应堆",
    "hoarder": "囤积癖",
    "banned_commissary": "禁入小卖部",
    "proficient_scavenger": "熟练拾荒",
    "careful_scavenger": "谨慎拾荒",
    "maimed": "残疾",
    "revolution_sympathizer": "革命同情者",
    "gambling_addiction": "赌瘾",
    "tapehead": "磁带迷",
    "wild_favorite": "狂野红人",
    "wild_bad_side": "狂野黑名单",
    "aug_menace": "改造体威胁",
    "lucky": "幸运",
    "unlucky": "倒霉",
    "handy": "手巧",
    "tinkerer": "修补匠",
    "vending": "自动贩卖",
    "short_sleeper": "短睡者",
    "working_pipes": "管道畅通",
    "illiterate": "文盲",
    "prime_real_estate": "黄金地段",
    "degenerative_osteoarthritis": "退行性关节炎",
    "known_snitch": "出名线人",
}

DESCRIPTIONS = {
    "saving": "开局多一点现金",
    "bad_lease": "房租更贵",
    "convict": "有前科，治安部盯得紧",
    "model_citizen": "名声好，检查更宽松",
    "well_connected": "人脉广，进货更便宜",
    "sycophant": "说好话更容易成交",
    "awful_negotiator": "砍价基本砍不动",
    "public_enemy": "被治安部重点关照",
    "alert": "更容易发现可疑顾客",
    "sawyer_crew": "索耶一伙会来捧场",
    "diplomatic": "谈判更容易谈拢",
    "intimidating": "吓唬顾客更好使",
    "side_street": "店面在偏街，人流少但租金低",
    "nicotine_addiction": "每天要抽烟，不抽会掉状态",
    "narcotic_addiction": "依赖药物",
    "alcohol_addiction": "依赖酒精",
    "secret_admirer": "有人偷偷给你送东西",
    "colorful_character": "小有名气，客人更杂",
    "blackmail": "有人拿把柄勒索你",
    "blabbermouth": "话多，容易走漏风声",
    "minimalist": "东西多反而不舒服",
    "ascetic": "苦修，享乐无效",
    "sentinel": "值守更强",
    "agoraphobia": "离开店会焦虑",
    "welfare_program": "有福利补贴",
    "makeshift_reactor": "自带土制反应堆",
    "hoarder": "舍不得扔东西",
    "banned_commissary": "被禁止进小卖部",
    "proficient_scavenger": "拾荒收益更高",
    "careful_scavenger": "拾荒更安全",
    "maimed": "有伤，体力上限低",
    "revolution_sympathizer": "和革命派关系好",
    "gambling_addiction": "手痒，会强制买彩票",
    "tapehead": "自带磁带机和磁带",
    "wild_favorite": "狂野网络里人缘好",
    "wild_bad_side": "狂野网络里被针对",
    "aug_menace": "改造体来得更早更凶",
    "lucky": "运气好",
    "unlucky": "运气差",
    "handy": "动手能力强",
    "tinkerer": "改装更省钱",
    "vending": "自动贩卖机收益更高",
    "short_sleeper": "睡得少也不累",
    "working_pipes": "管道状态更好",
    "illiterate": "看不懂文件",
    "prime_real_estate": "店面在黄金地段",
    "degenerative_osteoarthritis": "关节退化，行动受限",
    "known_snitch": "大家都知道你告密",
}


def load_catalog(path: str | Path | None = None) -> dict:
    target = Path(path) if path else Path(__file__).with_name(CATALOG_FILE)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {d["id"]: d for d in data if d.get("id")}


def label(perk_id: str) -> str:
    return LABELS.get(perk_id, perk_id)


def group(entry: dict) -> str:
    kind = int(entry.get("type", 0))
    if kind == 2:
        return "特殊"
    if int(entry.get("cost", 0)) < 0:
        return "缺陷（给点数）"
    return "正面"


def build_entries(ids, catalog: dict | None = None) -> list[dict]:
    """Definition objects exactly shaped like the ones the game writes."""
    catalog = catalog if catalog is not None else load_catalog()
    out = []
    for perk_id in ids:
        spec = catalog.get(perk_id, {})
        out.append(
            {
                "id": perk_id,
                "cost": int(spec.get("cost", 0)),
                "maxSlot": 0,
                "incompatiblePerks": list(spec.get("incompatible", [])),
                "type": int(spec.get("type", 0)),
                "mindEditable": bool(spec.get("mindEditable", False)),
                "rawName": None,
                "rawDescription": None,
            }
        )
    return out


def conflicts(ids, catalog: dict | None = None) -> list[tuple[str, str]]:
    """(a, b) pairs of chosen perks that cannot coexist."""
    catalog = catalog if catalog is not None else load_catalog()
    chosen = set(ids)
    out = []
    for perk_id in ids:
        spec = catalog.get(perk_id, {})
        for other in spec.get("incompatible", []):
            if other != perk_id and other in chosen:
                pair = tuple(sorted((perk_id, other)))
                if pair not in out:
                    out.append(pair)
    return out
