"""
Domain layer for "Probably Stolen" save editing.

Everything here is about *safety*: the game reads these files back verbatim, so
the editor only ever patches the exact character spans of the values you change
(see es3json.py), keeps a timestamped backup outside the folder the game scans,
and re-parses its own output to prove that nothing but the intended fields moved.
"""

from __future__ import annotations

import copy
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import es3json

GAME_NAME = "Probably Stolen"
COMPANY = "Questing Goose Studio"
BACKUP_DIR = "_editor_backups"

# Where Unity puts Application.persistentDataPath on Windows.
DEFAULT_SAVE_DIR = Path(os.environ.get("USERPROFILE", "")) / "AppData" / "LocalLow" / COMPANY / GAME_NAME

# The path to the player object inside every slot file.
P = ("playerStore", "value")
S = ("storeStation", "value")

INT, FLOAT, BOOL, STR = "int", "float", "bool", "str"


# --------------------------------------------------------------------------- #
# field specifications (the GUI builds itself from these)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Field:
    label: str
    path: tuple
    kind: str
    lo: float | None = None
    hi: float | None = None
    hint: str = ""
    step: int = 1


@dataclass(frozen=True)
class Group:
    title: str
    fields: list[Field]
    blurb: str = ""


MONEY_GROUP = Group(
    "金钱与经济",
    [
        Field("现金", P + ("playerCash",), INT, 0, 999_999_999, "店铺的可用资金"),
        Field("Wild 好感", P + ("wildFavor",), INT, -100, 999, "野狼网络那边的面子"),
        Field("距下次租金（天）", P + ("dayUntilRent",), INT, -1, 999, "调到很大可以拖很久"),
        Field("每周租金", P + ("rentValue",), INT, 0, 100_000, "收租时扣的钱"),
        Field("距还贷（天）", P + ("dayUntilLoan",), INT, -1, 999, ""),
        Field("贷款金额", P + ("loanValue",), INT, 0, 1_000_000, ""),
        Field("ODIN 预算", P + ("odinBudget",), INT, 0, 1_000_000, "治安部那边的额度"),
        Field("焚化炉法拍比例 %", P + ("foreclosePercentage",), INT, 0, 100, ""),
        Field("违禁品罚款比例 %", P + ("contrabandFinePercentage",), INT, 0, 100, ""),
        Field("垃圾堆等级", P + ("garbageLevel",), INT, 0, 100, ""),
        Field("零售加价 %", P + ("retailMarkup",), INT, 0, 500, "默认 15"),
        Field("违禁品加价·低 %", P + ("contrabandMarkupLow",), INT, 0, 500, ""),
        Field("违禁品加价·中 %", P + ("contrabandMarkupMid",), INT, 0, 500, ""),
        Field("违禁品加价·高 %", P + ("contrabandMarkupHigh",), INT, 0, 500, ""),
        Field("违禁品加价·极品 %", P + ("contrabandMarkupCritial",), INT, 0, 500, ""),
    ],
    "现金就是 playerCash —— 改完记得顺手把「天数」一起调，读档界面会同步。",
)

STATION_GROUP = Group(
    "车站与势力",
    [
        Field("当前天数", S + ("dayCounter",), INT, 1, 100_000, "读档界面显示的天数"),
        Field("客流量", S + ("traffic",), INT, 0, 1000, "越高客人越多，通常 0-100"),
        Field("顾客满意度", S + ("happiness",), INT, 0, 1000, "通常 0-100"),
        Field("治安秩序", S + ("order",), INT, 0, 1000, "通常 0-100"),
        Field("阴沟水纯度", S + ("todayGutterflowPurity",), INT, 0, 100_000, "满值约 10000"),
        Field("锈水纯度", S + ("todayRustwaterPurity",), INT, 0, 100_000, "满值约 10000"),
        Field("下层区动荡", S + ("storeOperationManager", "lowerLevelUnrest"), INT, 0, 100, ""),
        Field("上层区好感", S + ("storeOperationManager", "upperLevelFriendliness"), INT, 0, 100, ""),
        Field("革命军势力", S + ("storeOperationManager", "revPower"), INT, 0, 100, ""),
        Field("治安部势力", S + ("storeOperationManager", "secPower"), INT, 0, 100, ""),
        Field("黑市势力", S + ("storeOperationManager", "blackMarketPower"), INT, 0, 100, ""),
    ],
    "客流/满意度/秩序都是 0-100 的百分比；水质满值大约是 10000。",
)

CHARACTER_GROUP = Group(
    "角色与健康",
    [
        Field("幸运值", P + ("healthData", "luck"), INT, 0, 100, "影响赌博与鉴定"),
        Field("伤势等级", P + ("healthData", "woundState"), INT, 0, 10, "0 = 无伤"),
        Field("哨兵储备", P + ("healthData", "sentinelPool"), INT, 0, 1000, ""),
        Field("液体幸运剩余天数", P + ("healthData", "liquidLuckDaysLeft"), INT, 0, 999, ""),
        Field("伤势稳定", P + ("healthData", "isWoundStable"), BOOL, hint="勾选 = 稳定"),
        Field("新伤未处理", P + ("healthData", "isWoundedFresh"), BOOL, hint="勾选 = 未处理"),
        Field("距上次尼古丁（天）", P + ("healthData", "daysSinceNicotineConsumption"), INT, 0, 9999, ""),
        Field("距上次酒精（天）", P + ("healthData", "daysSinceAlcoholConsumption"), INT, 0, 9999, ""),
        Field("距上次毒品（天）", P + ("healthData", "daysSinceNarcoticConsumption"), INT, 0, 9999, ""),
        Field("距上次戒断缓解", P + ("healthData", "daysSinceNarcoticRelief"), INT, 0, 9999, ""),
        Field("距上次哨兵注射（天）", P + ("healthData", "daysSinceSentinelInjection"), INT, 0, 9999, ""),
        Field("昨夜去过后巷", P + ("healthData", "wentToAfterhoursLastNight"), BOOL, hint="勾选 = 去过"),
    ],
    "健康数据基本都在 playerStore.healthData 里，都是安全可改的计数器。",
)


# --------------------------------------------------------------------------- #
# save folder discovery
# --------------------------------------------------------------------------- #


def candidate_save_dirs() -> list[Path]:
    dirs = [DEFAULT_SAVE_DIR]
    # Unity also honours the OneDrive-redirected Documents/LocalLow in rare setups.
    for env in ("USERPROFILE", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            p = Path(base) / "LocalLow" / COMPANY / GAME_NAME
            if p not in dirs:
                dirs.append(p)
    return dirs


def discover_save_dir() -> Path | None:
    for d in candidate_save_dirs():
        if d.is_dir() and any(d.glob("save_*.es3")):
            return d
    return None


# --------------------------------------------------------------------------- #
# profile / slots
# --------------------------------------------------------------------------- #


@dataclass
class SlotInfo:
    slot_id: int
    path: Path | None
    credit: int | None = None
    day: int | None = None
    start_name: str | None = None
    scenario: str | None = None
    saved_at: str | None = None
    version: str | None = None

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.exists()

    def title(self) -> str:
        if not self.exists:
            return f"槽位 {self.slot_id}（空）"
        bits = [f"槽位 {self.slot_id}"]
        if self.day is not None:
            bits.append(f"第{self.day}天")
        if self.credit is not None:
            bits.append(f"₵{self.credit:,}")
        return "  ·  ".join(bits)

    def subtitle(self) -> str:
        if not self.exists:
            return "没有存档文件"
        bits = []
        if self.start_name:
            bits.append(self.start_name)
        if self.scenario:
            bits.append(self.scenario)
        if self.saved_at:
            bits.append(self.saved_at)
        return "  ·  ".join(bits)


class Profile:
    """The folder holding save_1.es3, save_2.es3 ... plus saves_index.es3."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.backup_dir = self.folder / BACKUP_DIR

    # -- files ---------------------------------------------------------------

    def slot_path(self, slot_id: int) -> Path:
        return self.folder / f"save_{slot_id}.es3"

    def index_path(self) -> Path:
        return self.folder / "saves_index.es3"

    def settings_path(self) -> Path:
        return self.folder / "SaveFile.es3"

    def exists(self) -> bool:
        return self.folder.is_dir()

    # -- reading -------------------------------------------------------------

    def _index(self) -> es3json.Document | None:
        path = self.index_path()
        if not path.exists():
            return None
        return es3json.Document.load(str(path))

    def slots(self) -> list[SlotInfo]:
        index = self._index()
        found: dict[int, SlotInfo] = {}
        for path in sorted(self.folder.glob("save_*.es3")):
            stem = path.stem  # save_1 / save_1_day6_...
            parts = stem.split("_")
            if len(parts) != 2 or not parts[1].isdigit():
                continue  # history snapshots are hidden from the slot list
            sid = int(parts[1])
            found[sid] = SlotInfo(slot_id=sid, path=path)

        if index is not None:
            for key in index.root.keys_list():
                if not (isinstance(key, str) and key.startswith("preview_")):
                    continue
                sid = int(key.split("_", 1)[1])
                preview = index.root[key].get("value", default=None)
                if preview is None:
                    continue
                info = found.setdefault(sid, SlotInfo(slot_id=sid, path=None))
                info.credit = _as_int(preview.get("credit", default=None))
                info.day = _as_int(preview.get("dayCount", default=None))
                info.scenario = _text(preview.get("startScenarioId", default=None))
                info.version = _text(preview.get("version", default=None))
                info.start_name = _text(preview.get("startName", default=None))
                info.saved_at = _format_ticks(preview.get("lastSaveDateTime", default=None))

        return [found[k] for k in sorted(found)]

    def total_play_time(self) -> str | None:
        index = self._index()
        if index is None:
            return None
        node = index.root.get("totalPlayTimeSeconds", default=None)
        if node is None:
            return None
        node = es3json.unwrap(node)
        if node.kind != "number":
            return None
        seconds = float(node.value)
        hours, minutes = divmod(int(seconds) // 60, 60)
        return f"{hours} 小时 {minutes} 分钟"

    # -- backups -------------------------------------------------------------

    def backup(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.backup_dir / f"{path.stem}.{stamp}{path.suffix}"
        n = 1
        while dest.exists():
            dest = self.backup_dir / f"{path.stem}.{stamp}-{n}{path.suffix}"
            n += 1
        shutil.copy2(path, dest)
        return dest

    def list_backups(self) -> list[Path]:
        if not self.backup_dir.is_dir():
            return []
        return sorted(self.backup_dir.glob("*.es3"), key=lambda p: p.stat().st_mtime, reverse=True)

    def prune_backups(self, keep: int = 40) -> None:
        backups = self.list_backups()
        for old in backups[keep:]:
            try:
                old.unlink()
            except OSError:
                pass


def _as_int(node) -> int | None:
    if node is None:
        return None
    node = es3json.unwrap(node)
    if node.kind != "number":
        return None
    return int(node.value)


def _text(node) -> str | None:
    if node is None:
        return None
    node = es3json.unwrap(node)
    if node.kind != "string":
        return None
    return node.value


def _format_ticks(node) -> str | None:
    if node is None or node.kind != "object":
        return None
    ticks = node.get("ticks", default=None)
    if ticks is None:
        return None
    try:
        return (time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ticks.value) / 10_000_000 - 62135596800)))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# the editor for one slot
# --------------------------------------------------------------------------- #


class EditError(RuntimeError):
    pass


class SlotEditor:
    """Loads one slot, stages path->value edits, then writes them safely."""

    def __init__(self, profile: Profile, slot_id: int):
        self.profile = profile
        self.slot_id = slot_id
        self.path = profile.slot_path(slot_id)
        if not self.path.exists():
            raise EditError(f"找不到存档文件 {self.path}")
        self.doc = es3json.Document.load(str(self.path))
        self._original = self.doc.root.to_python()
        self._staged: dict[tuple, tuple[Any, str]] = {}
        self._inner: dict[str, es3json.Document] = {}
        self._inner_original: dict[str, str] = {}

    # -- basic access --------------------------------------------------------

    def node(self, path: Iterable[Any]):
        return self.doc.root.get(*path)

    def value(self, path: Iterable[Any]) -> Any:
        return self.node(path).to_python()

    def original_value(self, path: Iterable[Any]) -> Any:
        return _get_in(self._original, path)

    def read(self, path: Iterable[Any]) -> Any:
        """Value as it would be after saving (staged if pending)."""
        key = tuple(path)
        if key in self._staged:
            return self._staged[key][0]
        return self.value(key)

    # -- staging -------------------------------------------------------------

    def stage(self, path: Iterable[Any], value: Any, label: str = "") -> None:
        key = tuple(path)
        text = label or _label_for(key)
        previous = self._staged.get(key)
        if previous and previous[1] and text not in previous[1]:
            # several item operations collapse into one staged string; keep both
            text = previous[1] + "；" + text
        self._staged[key] = (value, text)

    def unstage(self, path: Iterable[Any]) -> None:
        self._staged.pop(tuple(path), None)

    def clear_staged(self) -> None:
        self._staged.clear()

    def staged(self) -> dict[tuple, tuple[Any, str]]:
        return dict(self._staged)

    def changes(self) -> list[tuple[str, Any, Any]]:
        """(label, old, new) for everything staged that actually differs."""
        out = []
        for path, (value, label) in self._staged.items():
            old = self.original_value(path)
            if _values_equal(old, value):
                continue
            out.append((label, old, value))
        return out

    def count_changes(self) -> int:
        return len(self.changes())

    # -- inner inventory documents ------------------------------------------

    INV_KEYS = {
        "mainInvJSON": "主背包",
        "backInvJSON": "备用包",
        "showcaseInvJSON": "展示柜",
        "vendingMachineInvJSON": "自动贩卖机",
        "vendingFountainInvJSON": "饮水机",
        "trashcanInvJSON": "垃圾桶",
        "hiddenInvJSON": "暗格",
        "soldInvJSON": "已售出",
        "docInvJSON": "档案袋",
        "cassettePlayerInvJSON": "随身听",
        "hirelingInvJSON": "雇员背包",
    }

    def inventories(self) -> list[tuple[str, str, int]]:
        """(field key, chinese label, item count) for the bags that exist."""
        out = []
        for key, label in self.INV_KEYS.items():
            node = self.doc.root.get(*P, key, default=None)
            if node is None or node.kind != "string":
                continue
            count = self.inventory(key).root.get("saveItems", default=None)
            out.append((key, label, len(count) if count is not None else 0))
        return out

    def inventory(self, key: str) -> es3json.Document:
        if key not in self._inner:
            raw = self.doc.root.get(*P, key).value
            self._inner_original[key] = raw
            self._inner[key] = es3json.Document(raw)
        return self._inner[key]

    def inventory_items(self, key: str) -> list[dict]:
        doc = self.inventory(key)
        items = doc.root.get("saveItems", default=None)
        if items is None:
            return []
        out = []
        for index, item in enumerate(items.items):
            out.append(
                {
                    "index": index,
                    "node": item,
                    "identifier": _text(item.get("identifier", default=None)) or "",
                    "name": _text(item.get("name", default=None)) or "",
                    "unique_id": _as_int(item.get("uniqueId", default=None)),
                    "unit_value": item.get("unitValue", default=None),
                    "unit_base": item.get("unitBaseValue", default=None),
                    "types": [t.value for t in item.get("itemTypes", default=None).items]
                    if item.get("itemTypes", default=None) is not None
                    else [],
                }
            )
        return out

    def stage_inventory_item(self, key: str, item_index: int, field_name: str, value: Any) -> None:
        doc = self.inventory(key)
        node = doc.root.get("saveItems", item_index, field_name)
        if node.kind == "number" and isinstance(node.value, float):
            value = float(value)
        elif node.kind == "number":
            value = int(value)
        doc.set(node, value)

    # -- structural inventory edits -----------------------------------------

    def inventory_add(self, key: str, template_text: str, parent_uuid: int = 0) -> str:
        """Clone a catalogue template into a bag; returns the item's name."""
        import inventory as inventory_mod

        try:
            name = es3json.Document(template_text).root.get("name").value
        except (KeyError, AttributeError, es3json.ES3Error):
            name = "物品"

        # uniqueId is one counter shared by every bag, mirrored in
        # playerStore.currentUniqueId. Take the larger of the two so a new item
        # can never collide with something the game creates later.
        try:
            counter = int(self.node(P + ("currentUniqueId",)).value)
        except (KeyError, AttributeError):
            counter = 0

        doc = self.inventory(key)
        inv = inventory_mod.Inventory(doc)
        new_unique_id = max(counter, inv.next_unique_id())
        inv.add_item(template_text, parent_uuid, 1, unique_id=new_unique_id)
        if new_unique_id >= counter:
            self.stage(P + ("currentUniqueId",), new_unique_id + 1, "物品编号计数器")
        self._commit_inventory(key, doc, f"添加物品「{name}」")
        return name

    def inventory_add_many(self, key: str, templates, parent_uuid: int = 0):
        """Add a whole batch of items to one bag in a single rewrite.

        Returns (added_pairs, skipped_templates, names_added).
        """
        import inventory as inventory_mod

        if not templates:
            return [], [], []
        try:
            counter = int(self.node(P + ("currentUniqueId",)).value)
        except (KeyError, AttributeError):
            counter = 0

        doc = self.inventory(key)
        inv = inventory_mod.Inventory(doc)
        base = max(counter, inv.next_unique_id())

        # pack the big pieces first; small ones fill the gaps afterwards
        ordered = sorted(templates, key=lambda t: -_template_area(t))
        added, skipped = inv.add_many(ordered, parent_uuid, 1, unique_base=base)
        if not added:
            return [], skipped, []
        if base + len(added) > counter:
            self.stage(P + ("currentUniqueId",), base + len(added), "物品编号计数器")
        names = [ident for _uuid, ident in added]
        self._commit_inventory(key, doc, f"批量添加 {len(added)} 件物品")
        self.ensure_item_features(self._pairs_from_uuids(key, [uuid for uuid, _ident in added]))
        return added, skipped, names

    def inventory_add_forest(self, key: str, roots, parent_uuid: int = 0):
        """Add captured item trees (containers + their contents) into a bag.

        Roots are placed by the normal free-spot logic; every nested child is put
        back on the exact cell it had inside its container, so the original
        layout is reproduced instead of guessed.

        Returns (added_roots, skipped_roots, items_added, containers_added).
        """
        import inventory as inventory_mod

        state = {
            "uid": 0,
            "items": 0,
            "containers": 0,
            "pairs": [],
        }
        try:
            counter = int(self.node(P + ("currentUniqueId",)).value)
        except (KeyError, AttributeError):
            counter = 0
        state["uid"] = max(counter, inventory_mod.Inventory(self.inventory(key)).next_unique_id())

        added_roots = 0
        skipped_roots = 0
        for root in roots:
            ok = self._add_tree(key, root, parent_uuid, getattr(root, "node", 1), state)
            if ok:
                added_roots += 1
            else:
                skipped_roots += 1

        if state["items"]:
            if state["uid"] > counter:
                self.stage(P + ("currentUniqueId",), state["uid"], "物品编号计数器")
            self._commit_inventory(key, self.inventory(key), f"批量添加 {state['items']} 件物品")
            self.ensure_item_features(state.get("pairs", []))
        return added_roots, skipped_roots, state["items"], state["containers"]

    def _add_tree(self, key: str, node, parent_uuid: int, node_id: int, state: dict) -> bool:
        import inventory as inventory_mod
        import presets as presets_mod

        if isinstance(node, presets_mod.PresetNode):
            template = node.template
            children = node.children
        else:
            template = node["template"]
            children = node.get("children", [])

        doc = self.inventory(key)
        inv = inventory_mod.Inventory(doc)
        try:
            added, _skipped = inv.add_many([template], parent_uuid, node_id, unique_base=state["uid"])
        except Exception:
            return False
        if not added:
            return False
        new_uuid = added[0][0]
        state["uid"] += 1
        state["items"] += 1
        if children:
            state["containers"] += 1
        self._inner[key] = es3json.Document(doc.render())
        state["pairs"].append((self._unique_id_of(key, new_uuid), template))

        for child in children:
            child_node = child.node if isinstance(child, presets_mod.PresetNode) else child.get("node", 1)
            child_pos = (child.x, child.y) if isinstance(child, presets_mod.PresetNode) else (
                child.get("x", 0), child.get("y", 0)
            )
            self._add_child(key, child, new_uuid, child_node, child_pos, state)
        return True

    def _add_child(self, key: str, child, parent_uuid: int, node_id: int, pos, state: dict) -> bool:
        import inventory as inventory_mod
        import presets as presets_mod

        template = child.template if isinstance(child, presets_mod.PresetNode) else child["template"]
        children = child.children if isinstance(child, presets_mod.PresetNode) else child.get("children", [])

        doc = self.inventory(key)
        inv = inventory_mod.Inventory(doc)
        try:
            added, _skipped = inv.add_many(
                [template], parent_uuid, node_id, unique_base=state["uid"], positions=[pos]
            )
        except Exception:
            return False
        if not added:
            return False
        new_uuid = added[0][0]
        state["uid"] += 1
        state["items"] += 1
        if children:
            state["containers"] += 1
        self._inner[key] = es3json.Document(doc.render())
        state["pairs"].append((self._unique_id_of(key, new_uuid), template))

        for sub in children:
            sub_node = sub.node if isinstance(sub, presets_mod.PresetNode) else sub.get("node", 1)
            sub_pos = (sub.x, sub.y) if isinstance(sub, presets_mod.PresetNode) else (
                sub.get("x", 0), sub.get("y", 0)
            )
            self._add_child(key, sub, new_uuid, sub_node, sub_pos, state)
        return True

    def inventory_update_many(self, key: str, updates, label: str = "批量修改物品") -> int:
        """Apply several item edits in ONE document rewrite.

        `updates` is [(index, apply_fn)]. Doing it per item would re-parse and
        re-validate the whole bag every time -- for a few dozen animals that is
        tens of seconds of frozen UI.
        """
        deduped = {}
        for index, apply_fn in updates:
            deduped[index] = apply_fn
        if not deduped:
            return 0

        doc = self.inventory(key)
        try:
            items = doc.root.get("saveItems")
        except (KeyError, AttributeError):
            return 0

        done = 0
        for index, apply_fn in deduped.items():
            if not (0 <= index < len(items.items)):
                continue
            apply_fn(doc, items.items[index])
            done += 1
        if done:
            self._commit_inventory(key, doc, f"{label}（{done} 件）")
        return done

    def inventory_remove(self, key: str, index: int) -> str:
        """Delete one item (and its references) from a bag; returns its name."""
        import inventory as inventory_mod

        doc = self.inventory(key)
        inv = inventory_mod.Inventory(doc)
        items = inv.items()
        if not 0 <= index < len(items):
            raise EditError("要删除的物品不存在")
        name = items[index].name
        inv.remove_item(index)
        self._commit_inventory(key, doc, f"删除物品「{name}」")
        return name

    def inventory_update_item(self, key: str, index: int, apply_fn, label: str = "编辑物品") -> str:
        """Run pply_fn(doc, item_node) on one item, then validate and stage."""
        import inventory as inventory_mod

        doc = self.inventory(key)
        items = inventory_mod.Inventory(doc).items()
        if not 0 <= index < len(items):
            raise EditError("要编辑的物品不存在")
        name = items[index].name
        apply_fn(doc, items[index].node)
        self._commit_inventory(key, doc, f"{label}「{name}」")
        return name

    def _unique_id_of(self, key: str, index: int) -> int:
        try:
            node = self.inventory(key).root.get("saveItems").items[index].get("uniqueId")
            return int(node.value)
        except (KeyError, IndexError, AttributeError, TypeError):
            return -1

    def repair_item_features(self) -> int:
        """Scan every bag and give feature-less durability/purity items their records.

        Its main use is undoing the damage older versions of this tool did when
        they cloned items without cloning their feature records.
        """
        import itemprops

        pairs = []
        for key in [k for k, _l, _c in self.inventories()]:
            doc = self.inventory(key)
            try:
                items = doc.root.get("saveItems")
            except (KeyError, AttributeError):
                continue
            for index, node in enumerate(items.items):
                if index == 0:
                    continue
                names = {t["identifier"] for t in itemprops.read_tags(node)}
                if not ({"DURABILITY_TAG", "INGOT_PURITY_TAG"} & names):
                    continue
                unique = node.get("uniqueId", default=None)
                if unique is None:
                    continue
                pairs.append((int(unique.value), doc.slice(node)))
        return self.ensure_item_features(pairs)

    def ensure_item_features(self, pairs) -> int:
        """Give freshly created items the feature records the game expects.

        pairs is [(unique_id, template_text)].  Features live outside the item
        (playerStore.savedItemFeatureList) and are re-attached by uniqueId when a
        save loads; without a record the item has no durability/purity feature and
        every tooltip attempt throws.
        """
        path = P + ("savedItemFeatureList",)
        try:
            node = self.node(path)
        except (KeyError, IndexError, TypeError, AttributeError):
            return 0

        staged = self._staged.get(path)
        records = list(staged[0]) if staged else node.to_python()
        if not isinstance(records, list):
            return 0

        donors = {}
        for record in records:
            if record.get("identifier") == "INGOT_PURITY_TAG":
                condition = record.get("fakeCondition") or {}
                donors[("INGOT_PURITY_TAG", condition.get("identifier"))] = record

        have = {(r.get("identifier"), r.get("parentItemUniqueId")) for r in records}
        added = 0
        for uid, template in pairs:
            for record in feature_records_for(template, uid, donors):
                key = (record["identifier"], uid)
                if key in have:
                    continue
                records.append(record)
                have.add(key)
                added += 1
        if not added:
            return 0
        self.stage(path, records, f"补 {added} 条物品特性记录")
        return added

    def _pairs_from_uuids(self, key: str, uuids) -> list:
        doc = self.inventory(key)
        try:
            items = doc.root.get("saveItems")
        except (KeyError, AttributeError):
            return []
        pairs = []
        for uid in uuids:
            if not (0 <= uid < len(items.items)):
                continue
            text = doc.slice(items.items[uid])
            try:
                unique_id = int(es3json.Document(text).root.get("uniqueId").value)
            except (es3json.ES3Error, KeyError, AttributeError, TypeError):
                continue
            pairs.append((unique_id, text))
        return pairs

    def _commit_inventory(self, key: str, doc, label: str) -> None:
        """Validate the edited bag, swap in a fresh document and stage the text."""
        import inventory as inventory_mod

        rendered = doc.render()
        try:
            fresh = es3json.Document(rendered)
        except es3json.ES3Error as exc:
            raise EditError(f"背包改完后无法解析，已中止：{exc}") from exc
        problems = inventory_mod.Inventory(fresh).check()
        if problems:
            raise EditError("背包结构自检失败，已中止（原文件未改动）：" + "；".join(problems[:3]))
        self._inner[key] = fresh
        self.stage(P + (key,), rendered, label)

    def _sync_inner_to_outer(self) -> None:
        for key, doc in self._inner.items():
            if not doc.dirty():
                continue
            new_text = doc.render()
            if new_text != self._inner_original[key]:
                self.stage(P + (key,), new_text, f"{self.INV_KEYS.get(key, key)} 物品数据")

    # -- committing ----------------------------------------------------------

    def build_documents(self) -> tuple[str, str | None]:
        """Render the slot file (and the index file if previews need syncing)."""
        self._sync_inner_to_outer()

        # 1. apply every staged edit to the span document
        self.doc.reset()
        for path, (value, _label) in self._staged.items():
            try:
                node = self.doc.root.get(*path)
            except (KeyError, IndexError, TypeError) as exc:
                raise EditError(f"存档里找不到字段 {path}: {exc}") from exc
            coerced = _coerce(node, value)
            if node.kind in ("object", "array"):
                # whole-container replacement (e.g. clearing a dictionary)
                self.doc.set_any(node, coerced)
            else:
                self.doc.set(node, coerced)

        slot_text = self.doc.render()

        # 2. verify by re-parsing and comparing against the intended result
        expected = copy.deepcopy(self._original)
        for path, (value, _label) in self._staged.items():
            _set_in(expected, path, _coerce_plain(self.doc.root.get(*path).to_python(), value))

        try:
            reparsed = es3json.loads(slot_text)
        except es3json.ES3Error as exc:
            raise EditError(f"生成的文件无法解析，已中止：{exc}") from exc

        if reparsed != expected:
            raise EditError("自检失败：写出的内容和预期不一致，已中止（原文件未改动）")

        # 3. keep the load-menu preview in sync with the edited values
        index_text = self._build_index(only_if_changed=True)
        return slot_text, index_text

    def _build_index(self, only_if_changed: bool = True) -> str | None:
        index_path = self.profile.index_path()
        if not index_path.exists():
            return None
        try:
            index = es3json.Document.load(str(index_path))
        except Exception:
            return None

        preview = index.root.get(f"preview_{self.slot_id}", default=None)
        if preview is None:
            return None
        value_node = preview.get("value", default=None)
        if value_node is None:
            return None

        credit = self.read(P + ("playerCash",))
        day = self.read(S + ("dayCounter",))

        changed = False
        for key, new_value in (("credit", credit), ("dayCount", day)):
            target = value_node.get(key, default=None)
            if target is None:
                continue
            if not _values_equal(target.to_python(), new_value):
                index.set(target, _coerce(target, new_value))
                changed = True

        if not changed and only_if_changed:
            return None
        return index.render()

    def save(self, allow_running_game: bool = False) -> dict:
        """Write the edited slot (and previews) with backup + verification."""
        if not self.changes():
            return {"saved": False, "reason": "没有修改"}

        slot_text, index_text = self.build_documents()

        backups = []
        backup = self.profile.backup(self.path)
        if backup:
            backups.append(backup)
        _atomic_write(self.path, slot_text)

        if index_text is not None:
            index_path = self.profile.index_path()
            index_backup = self.profile.backup(index_path)
            if index_backup:
                backups.append(index_backup)
            _atomic_write(index_path, index_text)

        self.profile.prune_backups()

        # reload so the UI shows what is actually on disk now
        changes = self.changes()
        self._reload()
        return {"saved": True, "changes": changes, "backups": backups}

    def _reload(self) -> None:
        self.doc = es3json.Document.load(str(self.path))
        self._original = self.doc.root.to_python()
        self._staged.clear()
        self._inner.clear()
        self._inner_original.clear()

    # -- one-click actions (stage only, never write) -------------------------

    # -- 改造体（augments）------------------------------------------------------

    AUGMENT_PATHS = {
        "introduced": P + ("isAugIntroduced",),
        "ended": P + ("isAugEnded",),
        "presence": P + ("augPresence",),
        "next_day": P + ("nextAugSpawnDay",),
        "handled": P + ("storeClientManager", "isAugSpawnHandledToday"),
        "kills": P + ("runStats", "augsShot"),
    }

    # vanilla difficulty rules (Difficulty.Vanilla): (kill drop, escape rise)
    AUG_DIFFICULTY = {
        "cozy": (25, 10),
        "normal": (25, 10),
        "hard": (10, 15),
        "challenge": (8, 16),
    }
    AUG_BASELINE = 15  # Shotgun.Unlock writes augPresence = 15 when shooting is enabled

    def read_augments(self) -> dict:
        out = {}
        for name, path in self.AUGMENT_PATHS.items():
            try:
                out[name] = self.read(path)
            except (KeyError, IndexError, TypeError):
                out[name] = None
        try:
            out["day"] = int(self.read(S + ("dayCounter",)) or 0)
        except (KeyError, IndexError, TypeError):
            out["day"] = 0
        try:
            out["difficulty"] = self.read(P + ("difficultyId",)) or ""
        except (KeyError, IndexError, TypeError):
            out["difficulty"] = ""
        drop, rise = self.AUG_DIFFICULTY.get(out["difficulty"], (0, 0))
        out["kill_drop"] = drop
        out["escape_rise"] = rise
        out["escapes"] = self._aug_escapes(out["presence"], out["kills"], drop, rise)
        return out

    def _aug_escapes(self, presence, kills, drop: int, rise: int):
        """How many augments got away, from presence = 15 + escapes*rise - kills*drop."""
        if not rise:
            return None
        try:
            value = int(presence or 0) - self.AUG_BASELINE + int(kills or 0) * drop
        except (TypeError, ValueError):
            return None
        if value < 0:
            return 0
        return round(value / rise, 2)

    def action_aug_set_escapes(self, count: int) -> None:
        """Write the presence that corresponds to count escaped augments."""
        state = self.read_augments()
        rise = state["escape_rise"]
        if not rise:
            raise EditError("这个难度没有读到放跑惩罚值，无法换算")
        drop = state["kill_drop"]
        value = self.AUG_BASELINE + int(count) * rise - int(state["kills"] or 0) * drop
        self.stage(self.AUGMENT_PATHS["presence"], max(0, value), f"改造体：按放跑 {count} 次写存在感")

    def action_aug_spawn_now(self) -> None:
        """Make the next augment client due right away (today)."""
        state = self.read_augments()
        day = state["day"]
        self.stage(self.AUGMENT_PATHS["introduced"], True, "改造体：已引入")
        self.stage(self.AUGMENT_PATHS["ended"], False, "改造体：取消收尾")
        self.stage(self.AUGMENT_PATHS["handled"], False, "改造体：今日未处理")
        self.stage(self.AUGMENT_PATHS["next_day"], day, f"改造体：{day} 天刷出")

    def action_aug_delay(self, days: int = 7) -> None:
        state = self.read_augments()
        current = int(state["next_day"] or 0)
        base = max(current, state["day"])
        self.stage(self.AUGMENT_PATHS["next_day"], base + days, f"改造体：推迟 {days} 天")

    def action_aug_zero_presence(self) -> None:
        self.stage(self.AUGMENT_PATHS["presence"], 0, "改造体存在感 → 0")

    def action_aug_end(self) -> None:
        self.stage(self.AUGMENT_PATHS["ended"], True, "改造体：收尾")

    def action_aug_restart(self) -> None:
        self.stage(self.AUGMENT_PATHS["introduced"], True, "改造体：已引入")
        self.stage(self.AUGMENT_PATHS["ended"], False, "改造体：未收尾")
        self.stage(self.AUGMENT_PATHS["handled"], False, "改造体：重置今日")
        self.stage(self.AUGMENT_PATHS["presence"], 0, "改造体存在感 → 0")
        self.stage(self.AUGMENT_PATHS["next_day"], -1, "改造体：刷出日交给游戏")

    # -- 鉴定（appraisal）-------------------------------------------------------

    APPRAISAL_RANKS = (
        # (certificate id, attract chance %, progress needed, base fee)
        ("cert1", 33, 100, 20),
        ("cert2", 42, 150, 40),
        ("cert3", 48, 250, 60),
        ("cert4", 55, 0, 100),
    )

    def read_appraisal(self) -> dict:
        out = {}
        for name, path in (
            ("level", P + ("appraiserLevel",)),
            ("progress", P + ("appraisalProgress",)),
            ("introduced", P + ("isAppraisalIntroduced",)),
            ("introduced_day", P + ("appraisalIntroducedDay",)),
            ("declined", P + ("appraisalPromotionDeclined",)),
            ("days_missed", P + ("storeClientManager", "appraisalDaysMissed")),
        ):
            try:
                out[name] = self.read(path)
            except (KeyError, IndexError, TypeError):
                out[name] = None
        try:
            out["certs"] = [
                item["identifier"]
                for item in self.inventory_items("showcaseInvJSON")
                if str(item["identifier"]).startswith("cert")
            ]
        except Exception:
            out["certs"] = []
        level = int(out["level"] or 0)
        out["level"] = max(0, min(len(self.APPRAISAL_RANKS) - 1, level))
        chance = self.APPRAISAL_RANKS[out["level"]][1]
        missed = int(out["days_missed"] or 0)
        out["base_chance"] = chance
        out["chance"] = min(100, chance * (2 + missed) // 2)
        out["threshold"] = self.APPRAISAL_RANKS[out["level"]][2]
        out["cert_id"] = self.APPRAISAL_RANKS[out["level"]][0]
        out["cert_shown"] = out["cert_id"] in out["certs"]
        return out

    def action_appraisal_force_today(self) -> None:
        """Raise the missed-day counter so the daily roll is effectively 100%."""
        state = self.read_appraisal()
        needed = 0
        chance = state["base_chance"] or 1
        while min(100, chance * (2 + needed) // 2) < 100 and needed < 40:
            needed += 1
        self.stage(
            P + ("storeClientManager", "appraisalDaysMissed"),
            needed,
            f"鉴定：错过天数 → {needed}（明天必来）",
        )

    def action_appraisal_reset_missed(self) -> None:
        self.stage(P + ("storeClientManager", "appraisalDaysMissed"), 0, "鉴定：错过天数 → 0")

    def action_appraisal_set_level(self, level: int) -> None:
        level = max(0, min(len(self.APPRAISAL_RANKS) - 1, int(level)))
        self.stage(P + ("appraiserLevel",), level, f"鉴定等级 → {level}")
        self.stage(P + ("appraisalProgress",), 0.0, "鉴定进度 → 0")

    def action_appraisal_fill_progress(self) -> None:
        state = self.read_appraisal()
        threshold = state["threshold"] or 100
        self.stage(P + ("appraisalProgress",), float(threshold), f"鉴定进度 → {threshold}")

    def action_add_cash(self, amount: int) -> None:
        current = int(self.read(P + ("playerCash",)) or 0)
        self.stage(P + ("playerCash",), max(0, current + amount), f"现金 +{amount:,}")

    def action_set_cash(self, value: int) -> None:
        self.stage(P + ("playerCash",), int(value), f"现金 = {value:,}")

    def action_max_factions(self, amount: float = 100.0) -> None:
        reps = self.node(P + ("storeReputations",))
        for i, rep in enumerate(reps.items):
            self.stage(
                P + ("storeReputations", i, "amount"),
                float(amount),
                f"{rep['factionDisplay'].value} 声望 = {amount:g}",
            )
            perks = rep.get("perks", default=None)
            if perks is not None:
                for j, perk in enumerate(perks.items):
                    if perk.get("isUnlocked", default=None) is not None:
                        self.stage(
                            P + ("storeReputations", i, "perks", j, "isUnlocked"),
                            True,
                            f"{rep['factionDisplay'].value}·{perk['perkId'].value} 特权",
                        )

    def action_buy_all_upgrades(self, state: int = 2) -> None:
        upgrades = self.node(P + ("networkUpgrade",))
        for key in upgrades.keys_list():
            entry = upgrades[key]
            if entry.get("state", default=None) is None:
                continue
            self.stage(
                P + ("networkUpgrade", key, "state"),
                int(state),
                f"网络升级 {key} → {'已购买' if state == 2 else '可购买'}",
            )

    def action_unlock_services(self) -> None:
        services = self.node(P + ("storeService",))
        for i, service in enumerate(services.items):
            node = service.get("unlocked", default=None)
            if node is None:
                continue
            self.stage(
                P + ("storeService", i, "unlocked"),
                True,
                f"服务 {service['displayName'].value or service['id'].value}",
            )

    CRIME_TRACKERS = (
        "dirtyWaterTracker",
        "mislabeledWaterTracker",
        "badChemTrackerFraud",
        "badChemTrackerEndangerment",
        "bribeTracker",
        "badAmmoTracker",
        "badAmmoTrackerLegal",
        "endangermentItemTracker",
    )

    def action_clear_crime(self) -> None:
        sec = self.node(P + ("secData",))
        for index_field in ("crimeAmount", "crimeAmountTotal"):
            arr = sec.get(index_field, default=None)
            if arr is None:
                continue
            for i in range(len(arr)):
                self.stage(P + ("secData", index_field, i), 0, f"犯罪值 {index_field}[{i}]")
        for name in (
            "evidenceLevel",
            "dirtyWaterTracker",
            "mislabeledWaterTracker",
            "badChemTrackerFraud",
            "badChemTrackerEndangerment",
            "bribeTracker",
            "badAmmoTracker",
            "badAmmoTrackerLegal",
            "endangermentItemTracker",
            "state",
            "recommendedAction",
        ):
            node = sec.get(name, default=None)
            if node is not None and node.kind == "number":
                self.stage(P + ("secData", name), 0, f"犯罪记录 {name}")
        if self.node(P + ("wrongfulReportCount",)).kind == "number":
            self.stage(P + ("wrongfulReportCount",), 0, "误报次数")
        try:
            wanted = self.node(P + ("wantedStatus",))
        except KeyError:
            wanted = None
        if wanted is not None and wanted.kind == "object" and len(wanted):
            self.stage(P + ("wantedStatus",), {}, "通缉名单")
        for name, value in (("assignedCaseOfficer", "None"), ("note", "")):
            node = sec.get(name, default=None)
            if node is not None and node.kind == "string":
                self.stage(P + ("secData", name), value, f"案件信息 {name}")

    def action_skip_tutorials(self) -> None:
        self.stage(P + ("tutorialEnable",), False, "关闭教程")
        self.stage(P + ("skipIntro",), True, "跳过开场")
        for key in self.player_keys():
            if key.startswith("is") and key.endswith("Introduced"):
                node = self.node(P + (key,))
                if node.kind == "bool":
                    self.stage(P + (key,), True, f"已解锁 {key}")

    def action_unlock_features(self) -> None:
        flags = {
            "isNewspaperUnlocked": "报纸",
            "isVendingFountainUnlocked": "饮水机",
            "isShootingUnlocked": "射击",
            "isCartelExited": "脱离卡特尔",
            "isPropertyPaid": "已买下店铺产权",
            "hasBusinessPermit": "营业许可",
            "isNetworkIntroduced": "野狼网络",
            "isAfterhoursIntroduced": "后巷",
        }
        for key, label in flags.items():
            try:
                node = self.node(P + (key,))
            except KeyError:
                continue
            if node.kind == "bool":
                self.stage(P + (key,), True, f"解锁 {label}")

        exp = self.node(P + ("expeditionData",))
        if exp.get("isExpeditionUnlocked", default=None) is not None:
            self.stage(P + ("expeditionData", "isExpeditionUnlocked"), True, "解锁远征")
        locations = exp.get("expeditionLocations", default=None)
        if locations is not None:
            for i, loc in enumerate(locations.items):
                if loc.get("isUnlocked", default=None) is not None:
                    self.stage(
                        P + ("expeditionData", "expeditionLocations", i, "isUnlocked"),
                        True,
                        f"远征地点 {loc['displayName'].value}",
                    )

    def action_clear_wounds(self) -> None:
        mapping = {
            "woundState": 0,
            "isWoundStable": True,
            "isWoundedFresh": False,
            "daysSinceNicotineConsumption": 999,
            "daysSinceAlcoholConsumption": 999,
            "daysSinceNarcoticConsumption": 999,
        }
        for key, value in mapping.items():
            try:
                node = self.node(P + ("healthData", key))
            except KeyError:
                continue
            self.stage(P + ("healthData", key), value, f"健康 {key}")

    def action_delay_rent(self, days: int = 99) -> None:
        self.stage(P + ("dayUntilRent",), days, f"租金倒计时 = {days} 天")
        try:
            self.stage(P + ("dayUntilLoan",), days, f"还贷倒计时 = {days} 天")
        except KeyError:
            pass

    def action_restock_commissary(self) -> None:
        """Fill the commissary shelves to their per-item max stock."""
        data = self.node(P + ("commissaryData",))
        items = data.get("commissaryItemDatas", default=None)
        if items is None:
            return
        for i, item in enumerate(items.items):
            stock = item.get("StockCount", default=None)
            maximum = item.get("maxStock", default=None)
            if stock is None or maximum is None or stock.kind != "number":
                continue
            self.stage(
                P + ("commissaryData", "commissaryItemDatas", i, "StockCount"),
                int(maximum.value),
                f"补给站库存 {item['identifier'].value}",
            )

    def action_multiply_item_values(self, factor: float) -> None:
        for key, _label, _count in self.inventories():
            doc = self.inventory(key)
            items = doc.root.get("saveItems", default=None)
            if items is None:
                continue
            for item in items.items:
                for name in ("unitValue", "unitBaseValue", "lateUnitValue"):
                    node = item.get(name, default=None)
                    if node is None or node.kind != "number":
                        continue
                    new = node.value * factor
                    doc.set(node, int(round(new)) if isinstance(node.value, int) else float(new))
            # the outer string field is synced from the edited inner document in
            # _sync_inner_to_outer(), so nothing else needs staging here

    def player_keys(self) -> list[str]:
        return self.node(P).keys_list()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _template_area(text: str) -> int:
    import inventory as inventory_mod

    width, height = inventory_mod._template_size(text)
    return max(1, width) * max(1, height)


def _label_for(path: tuple) -> str:
    readable = " → ".join(str(p) for p in path if isinstance(p, str))
    return readable or str(path)


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


FEATURE_RECORD_FIELDS = (
    "featureType", "valueStage", "publicDisplay", "actualDisplay", "removedByTool",
    "valueModifier", "initiallyShown", "isFeatureExposed", "isExposing",
    "isFeatureDiscovered", "fakeCondition", "realCondition", "useCondition",
    "usePreExposeValue", "preExposeValueModifier", "isExposable", "isPublicHidden",
    "customIntValue1", "isDisabled", "identifier", "category", "isFeatureMatch",
)


def _tag_table(template_text: str) -> dict:
    """identifier -> {int, str, double} for one item's _values."""
    try:
        root = es3json.Document(template_text).root
    except es3json.ES3Error:
        return {}
    values = root.get("_values", default=None)
    if values is None or values.kind != "array":
        return {}
    table = {}
    for entry in values.items:
        ident = entry.get("<identifier>k__BackingField", default=None)
        if ident is None or ident.kind != "string":
            continue
        row = {}
        for field, kind in (("valueInt", "int"), ("internalValueString", "str"), ("valueDouble", "double")):
            node = entry.get(field, default=None)
            if node is not None:
                row[kind] = node.value
        table[ident.value] = row
    return table


def _durability_record(uid: int, current: int, maximum: int) -> dict:
    text = f"耐久度：{current}/{maximum}"
    return {
        "featureType": 0,
        "valueStage": 0,
        "parentItemUniqueId": uid,
        "publicDisplay": text,
        "actualDisplay": text,
        "removedByTool": None,
        "valueModifier": 0,
        "initiallyShown": True,
        "isFeatureExposed": False,
        "isExposing": False,
        "isFeatureDiscovered": False,
        "fakeCondition": None,
        "realCondition": None,
        "useCondition": False,
        "usePreExposeValue": True,
        "preExposeValueModifier": 0,
        "isExposable": True,
        "isPublicHidden": False,
        "customIntValue1": 0,
        "isDisabled": False,
        "identifier": "DURABILITY_TAG",
        "category": "DURABILITY_TAG",
        "isFeatureMatch": True,
    }


def _clone_feature_record(record: dict, uid: int) -> dict:
    out = {k: record.get(k) for k in FEATURE_RECORD_FIELDS}
    out["parentItemUniqueId"] = uid
    return out


def feature_records_for(template_text: str, uid: int, donors: dict) -> list[dict]:
    """The feature records an item needs so the game can rebuild it after a load.

    The game keeps features OUTSIDE the item (playerStore.savedItemFeatureList)
    and re-attaches them by uniqueId at load; an item born without its records
    ends up feature-less, which makes the tooltip path throw.
    """
    tags = _tag_table(template_text)
    records = []
    if "DURABILITY_TAG" in tags:
        current = int(tags["DURABILITY_TAG"].get("int") or 0)
        maximum = int(
            (tags.get("MAX_DURABILITY_TAG") or {}).get("int")
            or (tags.get("DEFAULT_DURABILITY_TAG") or {}).get("int")
            or current
        )
        records.append(_durability_record(uid, current, maximum))
    if "INGOT_PURITY_TAG" in tags:
        purity = tags["INGOT_PURITY_TAG"].get("str") or ""
        donor = donors.get(("INGOT_PURITY_TAG", purity)) or donors.get(("INGOT_PURITY_TAG", None))
        if donor:
            records.append(_clone_feature_record(donor, uid))
    return records


def _coerce(node, value: Any) -> Any:
    """Make sure a value has the same primitive type as the node it replaces."""
    if node.kind == "bool":
        return bool(value)
    if node.kind == "number":
        return float(value) if isinstance(node.value, float) else int(round(float(value)))
    if node.kind == "string":
        return str(value)
    return value


def _coerce_plain(node, value: Any) -> Any:
    """Same coercion, but against a python-value reference instead of a node."""
    if isinstance(node, bool):
        return bool(value)
    if isinstance(node, float):
        return float(value)
    if isinstance(node, int):
        return int(round(float(value)))
    return value


def _get_in(data: Any, path: Iterable[Any]) -> Any:
    cur = data
    for step in path:
        cur = cur[step]
    return cur


def _set_in(data: Any, path: Iterable[Any], value: Any) -> None:
    steps = list(path)
    cur = data
    for step in steps[:-1]:
        cur = cur[step]
    cur[steps[-1]] = value


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
