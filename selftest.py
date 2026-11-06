"""
Self-test for the save editor.

It never touches your real saves: the profile folder is copied to a temp
directory, edits are applied there, and the result is verified field by field.

    python selftest.py                 # test against the live save folder
    python selftest.py <save folder>   # test against a specific folder
"""

from __future__ import annotations

import os
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import animal_dna
import es3json
import inventory
import itemcatalog
import itemprops
import modules
import perks
import presets
import savegame
import seed_dna

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}{('  — ' + detail) if detail else ''}")


def diff_paths(a, b, path=()) -> list:
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a.keys() | b.keys():
            if key not in a or key not in b:
                out.append(path + (key,))
            else:
                out.extend(diff_paths(a[key], b[key], path + (key,)))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(path + (("len", len(a), len(b)),))
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff_paths(x, y, path + (i,)))
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(float(a) - float(b)) > 1e-9:
            out.append(path)
    elif a != b:
        out.append(path)
    return out


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else savegame.discover_save_dir()
    if source is None:
        print("找不到存档目录")
        return 2
    print(f"存档目录: {source}\n")

    work = Path(tempfile.mkdtemp(prefix="ps_saveedit_test_"))
    profile_dir = work / "Probably Stolen"
    shutil.copytree(source, profile_dir)
    try:
        return run_checks(profile_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)



def _pick_bag(editor, width: int = 4, height: int = 3) -> str | None:
    """First bag that still has room for a width x height item, if any."""
    for key, _label, _count in editor.inventories():
        try:
            inventory.Inventory(editor.inventory(key)).free_spot(0, width, height)
            return key
        except inventory.InventoryError:
            continue
        except Exception:
            continue
    return None


def run_checks(profile_dir):


    # --- parser fidelity on the copy -------------------------------------
    for name in sorted(p.name for p in profile_dir.glob("*.es3")):
        doc = es3json.Document.load(str(profile_dir / name))
        original = (profile_dir / name).read_text(encoding="utf-8-sig")
        check(f"解析/原样还原 {name}", doc.render() == original)

    profile = savegame.Profile(profile_dir)
    slots = [s for s in profile.slots() if s.exists]

    for info in slots:
        sid = info.slot_id
        before_bytes = {
            p.name: p.read_bytes() for p in profile_dir.glob("*.es3")
        }
        before_json = {
            p.name: es3json.load_file(str(p)) for p in profile_dir.glob("*.es3")
        }

        ed = savegame.SlotEditor(profile, sid)

        target_cash = 424242
        target_day = 55
        ed.stage(savegame.P + ("playerCash",), target_cash, "现金")
        ed.stage(savegame.S + ("dayCounter",), target_day, "天数")
        ed.stage(savegame.P + ("healthData", "luck"), 42, "幸运")

        faction_index = 0
        ed.stage(savegame.P + ("storeReputations", faction_index, "amount"), 99.5, "声望")

        upgrades = ed.node(savegame.P + ("networkUpgrade",))
        up_key = upgrades.keys_list()[0]
        ed.stage(savegame.P + ("networkUpgrade", up_key, "state"), 2, "升级")

        # inventory: bump the first real item's value
        inv_key = "backInvJSON" if "backInvJSON" in [i[0] for i in ed.inventories()] else ed.inventories()[0][0]

        items = ed.inventory_items(inv_key)
        if len(items) > 1:
            ed.stage_inventory_item(inv_key, 1, "unitValue", 7777)

        changed = ed.count_changes()
        check(f"槽位{sid}: 暂存了修改", changed > 0, f"{changed} 处")

        result = ed.save(allow_running_game=True)
        check(f"槽位{sid}: 写盘成功", result.get("saved") is True, str(result.get("backups")))
        check(f"槽位{sid}: 已生成备份", bool(result.get("backups")))

        after_json = {p.name: es3json.load_file(str(p)) for p in profile_dir.glob("*.es3")}

        # only the edited slot and the index may change
        for name in before_json:
            if name == f"save_{sid}.es3":
                continue
            if name == "saves_index.es3":
                continue
            check(f"槽位{sid}: {name} 未被动过", before_bytes[name] == (profile_dir / name).read_bytes())

        # exactly the intended fields moved
        diffs = diff_paths(before_json[f"save_{sid}.es3"], after_json[f"save_{sid}.es3"])
        expected_core = {
            (savegame.P + ("playerCash",)),
            (savegame.S + ("dayCounter",)),
            (savegame.P + ("healthData", "luck")),
            (savegame.P + ("storeReputations", faction_index, "amount")),
            (savegame.P + ("networkUpgrade", up_key, "state")),
        }
        diff_set = set(diffs)
        unexpected = {d for d in diff_set if d not in expected_core and not (inv_key in d)}
        check(f"槽位{sid}: 只改了目标字段", not unexpected, f"意外差异: {list(unexpected)[:3]}")

        # values read back correctly
        reloaded = savegame.SlotEditor(profile, sid)
        check(f"槽位{sid}: 现金回读", reloaded.value(savegame.P + ("playerCash",)) == target_cash)
        check(f"槽位{sid}: 天数回读", reloaded.value(savegame.S + ("dayCounter",)) == target_day)
        check(f"槽位{sid}: 幸运回读", reloaded.value(savegame.P + ("healthData", "luck")) == 42)
        check(f"槽位{sid}: 升级状态回读", reloaded.value(savegame.P + ("networkUpgrade", up_key, "state")) == 2)
        if len(items) > 1:
            got = reloaded.inventory_items(inv_key)[1]["node"].get("unitValue").value
            check(f"槽位{sid}: 物品价值回读", got == 7777, f"得到 {got}")

        # the load-menu preview should now match
        idx = es3json.load_file(str(profile_dir / "saves_index.es3"))
        preview = idx.get(f"preview_{sid}")
        if preview:
            check(
                f"槽位{sid}: 读档预览已同步",
                preview["value"]["credit"] == target_cash and preview["value"]["dayCount"] == target_day,
                f"credit={preview['value']['credit']} day={preview['value']['dayCount']}",
            )

    # --- item catalogue + add/delete items --------------------------------
    if slots:
        sid = slots[0].slot_id
        catalog = itemcatalog.Catalog(profile_dir / "_test_catalog.json")
        catalog.harvest_profile(profile)
        check("物品百科: 从存档收录物品", len(catalog) >= 20, f"{len(catalog)} 件")

        ed = savegame.SlotEditor(profile, sid)
        inv_key = "mainInvJSON"
        before_items = len(ed.inventory_items(inv_key))
        others_before = {
            p.name: es3json.load_file(str(p)) for p in profile_dir.glob("save_*.es3")
        }

        picks = [e for e in catalog.all()][:3]
        for entry in picks:
            ed.inventory_add(inv_key, entry.template)
        after_items = len(ed.inventory_items(inv_key))
        check("添加物品: 数量增加", after_items == before_items + len(picks),
              f"{before_items} -> {after_items}")

        try:
            res = ed.save(allow_running_game=True)
            check("添加物品: 写盘成功", res.get("saved") is True)
        except savegame.EditError as exc:
            check("添加物品: 写盘成功", False, str(exc))

        reloaded = savegame.SlotEditor(profile, sid)
        inv = inventory.Inventory(reloaded.inventory(inv_key))
        check("添加物品: 结构不变量", not inv.check(), "; ".join(inv.check()[:2]))
        check("添加物品: 数量回读", len(inv.items()) == after_items)

        # the clone must not inherit another save's child references
        new_items = inv.items()[-len(picks):]
        check("添加物品: 克隆件无残留引用",
              all(not inventory._child_list(i.node) for i in new_items))

        # placement must stay inside the bag grid, or the item is invisible in game
        grid_w, grid_h = inv.grid_size(0)
        inside = all(i.x >= 0 and i.y >= 0 and i.x + i.width <= grid_w and i.y + i.height <= grid_h
                     for i in new_items)
        check("添加物品: 落在背包格子内", inside,
              f"grid {grid_w}x{grid_h}, pos " + str([(i.x, i.y, i.width, i.height) for i in new_items]))

        # itemShape is the canonical footprint and must stay at (0,0)
        shapes_ok = all(
            i.node.get("itemShape").get("<minX>k__BackingField").value == 0
            and i.node.get("itemShape").get("<minY>k__BackingField").value == 0
            for i in new_items
        )
        check("添加物品: 基础形状未被位移", shapes_ok)

        # unique ids must stay above the game's global counter
        counter = reloaded.value(savegame.P + ("currentUniqueId",))
        check("添加物品: 物品编号不与计数器冲突",
              all(i.unique_id < counter for i in new_items),
              f"ids {[i.unique_id for i in new_items]} < counter {counter}")

        # delete one and make sure everything renumbers cleanly
        removed_name = reloaded.inventory_remove(inv_key, 3)
        reloaded.save(allow_running_game=True)
        again = savegame.SlotEditor(profile, sid)
        inv2 = inventory.Inventory(again.inventory(inv_key))
        check("删除物品: 数量减少", len(inv2.items()) == after_items - 1,
              f"removed {removed_name}")
        check("删除物品: 结构不变量", not inv2.check(), "; ".join(inv2.check()[:2]))

        # --- every attribute of an item is editable ---------------------
        editor = savegame.SlotEditor(profile, sid)
        one = inventory.Inventory(editor.inventory(inv_key)).items()[1]
        original = itemprops.read_basic(one.node)
        original_texts = itemprops.read_texts(one.node)
        original_shapes = itemprops.read_shapes(one.node)
        tags = itemprops.read_tags(one.node)

        edited_basic = dict(original)
        edited_basic["unitValue"] = 7777
        edited_basic["name"] = "属性测试物品"
        edited_basic["unitBaseValue"] = 4321
        edited_texts = dict(original_texts)
        edited_texts["flavorText"] = "改过的风味文本"
        edited_texts["shortDescription"] = "改过的简介"
        edited_shapes = {k: dict(v) for k, v in original_shapes.items()}
        edited_shapes["itemModifiedShape"]["<minX>k__BackingField"] = 5
        edited_shapes["itemModifiedShape"]["<minY>k__BackingField"] = 3
        edited_types = ["ALPHA", "BETA"]
        tag_edits = {0: {"valueInt": 12345, "valueEnabled": True, "internalValueString": "abc"}}

        editor.inventory_update_item(
            inv_key, 1,
            lambda doc, node: itemprops.apply_edits(
                doc, node, edited_basic, edited_texts, edited_shapes, edited_types, tag_edits
            ),
            label="属性测试",
        )
        editor.save(allow_running_game=True)

        fresh = savegame.SlotEditor(profile, sid)
        item = inventory.Inventory(fresh.inventory(inv_key)).items()[1]
        got = itemprops.read_basic(item.node)
        got_texts = itemprops.read_texts(item.node)
        got_shapes = itemprops.read_shapes(item.node)
        got_types = itemprops.read_types(item.node)
        got_tags = itemprops.read_tags(item.node)

        check("属性编辑: 数值字段", got["unitValue"] == 7777 and got["unitBaseValue"] == 4321,
              f"value={got['unitValue']} base={got['unitBaseValue']}")
        check("属性编辑: 名称", got["name"] == "属性测试物品", got["name"])
        check("属性编辑: 文本字段",
              got_texts["flavorText"] == "改过的风味文本" and got_texts["shortDescription"] == "改过的简介")
        check("属性编辑: 形状坐标",
              got_shapes["itemModifiedShape"]["<minX>k__BackingField"] == 5
              and got_shapes["itemModifiedShape"]["<minY>k__BackingField"] == 3)
        check("属性编辑: 类型标签", got_types == ["ALPHA", "BETA"], str(got_types))
        check("属性编辑: 附加标签值",
              got_tags and got_tags[0].get("valueInt") == 12345
              and got_tags[0].get("internalValueString") == "abc"
              and got_tags[0].get("valueEnabled") is True,
              str(got_tags[0]) if got_tags else "no tags")
        check("属性编辑: _keys 与 _values 仍然对齐",
              len(item.node.get("_keys").items) == len(item.node.get("_values").items))
        check("属性编辑: 结构不变量",
              not inventory.Inventory(fresh.inventory(inv_key)).check())

        # the shape mask must be regenerated when w/h change
        shapes2 = {k: dict(v) for k, v in got_shapes.items()}
        shapes2["itemShape"]["<width>k__BackingField"] = 4
        shapes2["itemShape"]["<height>k__BackingField"] = 3
        fresh.inventory_update_item(
            inv_key, 1,
            lambda doc, node: itemprops.apply_edits(
                doc, node, got, got_texts, shapes2, got_types, {}
            ),
            label="形状测试",
        )
        fresh.save(allow_running_game=True)
        after_shape = savegame.SlotEditor(profile, sid)
        shape_node = inventory.Inventory(after_shape.inventory(inv_key)).items()[1].node.get("itemShape")
        check("属性编辑: 形状掩码随宽高重建",
              len(shape_node.get("data").items) == 12,
              f"{len(shape_node.get('data').items)} cells for 4x3")

        # --- containers: contents and amounts ---------------------------
        container_ed = savegame.SlotEditor(profile, sid)
        found = None
        for inv_name, _label, _count in container_ed.inventories():
            for i, it in enumerate(container_ed.inventory_items(inv_name)):
                info = itemprops.container_info(it["node"])
                if info and info.get("capacity") and info["total"]:
                    found = (inv_name, i, info)
                    break
            if found:
                break

        if found:
            inv_name, item_index, info = found
            capacity = int(info["capacity"]["value"])
            check("内容物: 读到容量与成分", capacity > 0 and len(info["parts"]) > 0,
                  f"cap={capacity} total={info['total']} parts={len(info['parts'])}")
            check("内容物: 摘要可读", "/" in itemprops.container_summary(
                container_ed.inventory_items(inv_name)[item_index]["node"]))

            water = next(p for p in info["parts"] if p["key"] == itemprops.PART_PREFIX + itemprops.WATER_PART)
            tag_edits = {p["index"]: {"valueInt": 0} for p in info["parts"]}
            tag_edits[water["index"]] = {"valueInt": capacity}

            container_ed.inventory_update_item(
                inv_name, item_index,
                lambda doc, node: itemprops.apply_edits(
                    doc, node,
                    itemprops.read_basic(node),
                    itemprops.read_texts(node),
                    itemprops.read_shapes(node),
                    itemprops.read_types(node),
                    tag_edits,
                ),
                label="内容物测试",
            )
            container_ed.save(allow_running_game=True)

            verify = savegame.SlotEditor(profile, sid)
            after_info = itemprops.container_info(verify.inventory_items(inv_name)[item_index]["node"])
            check("内容物: 灌满后总量等于容量", after_info["total"] == capacity,
                  f"{after_info['total']} vs {capacity}")
            nonzero = [(p["label"], p["amount"]) for p in after_info["parts"] if p["amount"]]
            check("内容物: 只剩纯水", len(nonzero) == 1 and nonzero[0][0] == "水", str(nonzero))
            check("内容物: 结构不变量",
                  not inventory.Inventory(verify.inventory(inv_name)).check())

        # --- custom modules: shape, affixes, base stats ----------------
        mod_ed = savegame.SlotEditor(profile, sid)
        mod_items = [it for it in mod_ed.inventory_items(inv_key) if modules.is_module(it["node"])]
        mod_add_key = _pick_bag(mod_ed)
        if mod_items and mod_add_key:
            template = mod_ed.inventory(inv_key).slice(mod_items[0]["node"])
            spec = {
                "name": "自检模组",
                "value": 4321,
                "tier": 7,
                "type": "MODULE_TYPE_CHEM",
                "effects": {tag: 33 for tag, _label in modules.EFFECT_FIELDS},
                "extra": "自检附加",
                "width": 3,
                "height": 2,
                "mask": [1, 0, 1, 1, 1, 0],
            }
            built = modules.build_module(template, spec)
            before_mods = len(mod_ed.inventory_items(mod_add_key))
            mod_ed.inventory_add(mod_add_key, built)
            check("自定义模组: 加入背包",
                  len(mod_ed.inventory_items(mod_add_key)) == before_mods + 1)
            mod_ed.save(allow_running_game=True)

            reread = savegame.SlotEditor(profile, sid)
            made = reread.inventory_items(mod_add_key)[-1]["node"]
            info = modules.read_module(made)
            check("自定义模组: 名称与估值",
                  info["name"] == "自检模组" and info["value"] == 4321,
                  f"{info['name']} / {info['value']}")
            check("自定义模组: 等级", info["tier"] == 7, str(info["tier"]))
            check("自定义模组: 类型切换", info["type"] == "MODULE_TYPE_CHEM", info["type"])
            check("自定义模组: 词条数值",
                  all(info["effects"].get(tag) == 33 for tag, _l in modules.EFFECT_FIELDS),
                  str(info["effects"]))
            check("自定义模组: 异形掩码",
                  info["mask"] == [1, 0, 1, 1, 1, 0] and (info["width"], info["height"]) == (3, 2),
                  f"{info['width']}x{info['height']} {info['mask']}")
            check("自定义模组: 结构不变量",
                  not inventory.Inventory(reread.inventory(mod_add_key)).check())

        # --- catalogue templates are clean (no stolen tag, normal price) ---
        cat = itemcatalog.Catalog()
        dirty = [e.identifier for e in cat.all()
                 if any(key in e.template for key in itemcatalog.STOLEN_KEYS)]
        check("物品百科: 没有赃物标记", not dirty, str(dirty[:3]))
        unclean = [e.identifier for e in cat.all()
                   if itemcatalog.normalize_template(e.template) != e.template]
        check("物品百科: 模板已净化", not unclean, str(unclean[:3]))
        if cat.get("nudka"):
            entry = cat.get("nudka")
            clean_ed = savegame.SlotEditor(profile, sid)
            clean_key = _pick_bag(clean_ed) or inv_key
            added_clean, _sk, _nm = clean_ed.inventory_add_many(clean_key, [entry.template])
            if added_clean:
                node_clean = clean_ed.inventory_items(clean_key)[added_clean[0][0]]["node"]
                tags_clean = {t["identifier"]: t for t in itemprops.read_tags(node_clean)}
                check("物品百科: 加出来的物品不带赃物",
                      "STOLEN_TAG" not in tags_clean, str(sorted(tags_clean)[:3]))
                price = (tags_clean.get("BOUGHT_PRICE_TAG") or {}).get("valueInt")
                value = node_clean.get("unitValue").value
                check("物品百科: 买价等于正常价", price in (None, value), f"{price} vs {value}")

        # --- created items must carry their feature records ------------
        feat_ed = savegame.SlotEditor(profile, sid)
        feat_add_key = _pick_bag(feat_ed)
        donor_item = None
        for key in [k for k, _l, _c in feat_ed.inventories()]:
            for it in feat_ed.inventory_items(key):
                tags = {t["identifier"] for t in itemprops.read_tags(it["node"])}
                if "DURABILITY_TAG" in tags:
                    donor_item = (key, it)
                    break
            if donor_item:
                break
        if donor_item and feat_add_key:
            donor_key, donor = donor_item
            donor_text = feat_ed.inventory(donor_key).slice(donor["node"])
            before_records = len(feat_ed.node(savegame.P + ("savedItemFeatureList",)).items)
            added, _skipped, _names = feat_ed.inventory_add_many(feat_add_key, [donor_text])
            feat_path = savegame.P + ("savedItemFeatureList",)
            staged = feat_ed.staged().get(feat_path)
            records = staged[0] if staged else feat_ed.node(feat_path).to_python()
            after_records = len(records)
            check("物品特性: 新建物品补了记录", after_records == before_records + 1,
                  f"{before_records} -> {after_records}")
            new_index = added[0][0]
            new_uid = feat_ed.inventory_items(feat_add_key)[new_index]["node"].get("uniqueId").value
            check("物品特性: 记录挂在正确的 uniqueId 上",
                  any(r.get("identifier") == "DURABILITY_TAG"
                      and r.get("parentItemUniqueId") == new_uid for r in records),
                  f"uniqueId={new_uid}")
            check("物品特性: 耐久显示写对",
                  any(r.get("identifier") == "DURABILITY_TAG"
                      and r.get("parentItemUniqueId") == new_uid
                      and r.get("publicDisplay", "").startswith("耐久度") for r in records))
            feat_ed.save(allow_running_game=True)
            reread = savegame.SlotEditor(profile, sid)
            check("物品特性: 写盘后没有缺记录的物品",
                  reread.repair_item_features() == 0)

        # --- seed DNA: read traits, rewrite the genome -----------------
        seed_ed = savegame.SlotEditor(profile, sid)
        seed_index = None
        for i, it in enumerate(seed_ed.inventory_items(inv_key)):
            if seed_dna.is_seed(it["node"]):
                seed_index = i
                break
        if seed_index is not None:
            before_dna = seed_dna.read_seed(seed_ed.inventory_items(inv_key)[seed_index]["node"])
            check("种子DNA: 读到性状", len(before_dna["traits"]) > 0,
                  str([t["label"] for t in before_dna["traits"]][:4]))
            levels = {name: 10 for name in seed_dna.TRAITS}
            levels["potent"] = 3
            stats = dict(before_dna["stats"])
            stats["REQUIRED_PROGRESS"] = 2
            stats["HARVEST_QUANTITY"] = 33
            stats["PLANT_HEALTH_INT"] = 777
            seed_ed.inventory_update_item(
                inv_key, seed_index,
                lambda doc, node: seed_dna.apply_dna(doc, node, stats, levels),
                label="种子DNA",
            )
            seed_ed.save(allow_running_game=True)

            seed_after = savegame.SlotEditor(profile, sid)
            node_after = seed_after.inventory_items(inv_key)[seed_index]["node"]
            dna = seed_dna.read_seed(node_after)
            check("种子DNA: 基因属性写回",
                  dna["stats"]["REQUIRED_PROGRESS"] == 2 and dna["stats"]["HARVEST_QUANTITY"] == 33,
                  f"{dna['stats']['REQUIRED_PROGRESS']} / {dna['stats']['HARVEST_QUANTITY']}")
            check("种子DNA: 全部性状都能写入",
                  len(dna["traits"]) == len(seed_dna.TRAITS),
                  f"{len(dna['traits'])} / {len(seed_dna.TRAITS)}")
            check("种子DNA: 已有性状满级",
                  all(t["level"] == 10 for t in dna["traits"] if t["key"] != "potent"),
                  str([(t["label"], t["level"]) for t in dna["traits"]][:3]))
            check("种子DNA: 新增性状被写入",
                  any(t["key"] == "potent" and t["level"] == 3 for t in dna["traits"]),
                  str([(t["label"], t["level"]) for t in dna["traits"] if t["key"] == "potent"]))
            check("种子DNA: 缺失的基因字段会补上",
                  dna["stats"]["PLANT_HEALTH_INT"] == 777,
                  str(dna["stats"].get("PLANT_HEALTH_INT")))
            check("种子DNA: 标签与键对齐",
                  len(node_after.get("_keys").items) == len(node_after.get("_values").items),
                  f"{len(node_after.get('_keys').items)} keys")
            check("种子DNA: 结构不变量",
                  not inventory.Inventory(seed_after.inventory(inv_key)).check())

        # --- quick-add presets: capture a bag (nesting included) --------
        store = presets.PresetStore(profile_dir / "_selftest_presets.json")
        src_ed = savegame.SlotEditor(profile, sid)
        preset = store.capture(src_ed, inv_key, "自检模板")
        check("快速模板: 捕获背包内容", preset.kinds > 0,
              f"{preset.kinds} 个顶层 / {preset.total} 件 / {preset.containers} 个箱子")
        check("快速模板: 嵌套被一起抓到",
              preset.total > preset.kinds or preset.containers > 0,
              f"total={preset.total} roots={preset.kinds} containers={preset.containers}")

        dst_ed = savegame.SlotEditor(profile, sid)
        dst_bag = _pick_bag(dst_ed) or "backInvJSON"
        before_bag = len(dst_ed.inventory_items(dst_bag))
        roots_ok, roots_skipped, items, containers = dst_ed.inventory_add_forest(dst_bag, preset.roots)
        check("快速模板: 批量添加有结果", roots_ok > 0,
              f"roots {roots_ok} skipped {roots_skipped} items {items}")
        dst_ed.save(allow_running_game=True)

        after_ed = savegame.SlotEditor(profile, sid)
        check("快速模板: 数量等于加入数",
              len(after_ed.inventory_items(dst_bag)) == before_bag + items,
              f"{before_bag} -> {len(after_ed.inventory_items(dst_bag))}, added {items}")
        after_inv = inventory.Inventory(after_ed.inventory(dst_bag))
        check("快速模板: 结构不变量", not after_inv.check(), "; ".join(after_inv.check()[:2]))

        if containers:
            with_kids = [it for it in after_inv.items() if inventory._child_list(it.node)]
            check("快速模板: 箱子里的东西也回来了", len(with_kids) >= min(containers, 1),
                  f"{len(with_kids)} 个容器有内容")
            deep = max(len(inventory._child_list(it.node)) for it in with_kids) if with_kids else 0
            check("快速模板: 嵌套层级保留", deep > 0, f"最多 {deep} 个直接子件")

        if items:
            new_ids = {it.node.get("uniqueId").value for it in after_inv.items()}
            check("快速模板: 编号唯一", len(new_ids) == len(after_inv.items()),
                  f"{len(new_ids)} unique / {len(after_inv.items())}")

        # --- 改造体 (augments) -----------------------------------------
        aug_ed = savegame.SlotEditor(profile, sid)
        before_aug = aug_ed.read_augments()
        check("改造体: 能读到状态", before_aug["day"] is not None and before_aug["presence"] is not None,
              str({k: before_aug[k] for k in ('introduced', 'ended', 'presence', 'next_day')}))

        aug_ed.action_aug_spawn_now()
        after_spawn = aug_ed.read_augments()
        check("改造体: 立刻刷出 = 今天", after_spawn["next_day"] == after_spawn["day"],
              f"{after_spawn['next_day']} vs {after_spawn['day']}")
        check("改造体: 立刻刷出会取消收尾与今日已处理",
              after_spawn["introduced"] and not after_spawn["ended"] and not after_spawn["handled"])

        aug_ed.action_aug_zero_presence()
        check("改造体: 存在感清零", aug_ed.read_augments()["presence"] == 0)

        aug_ed.action_aug_restart()
        restarted = aug_ed.read_augments()
        check("改造体: 重开一条线", restarted["next_day"] == -1 and not restarted["ended"],
              str(restarted["next_day"]))

        rise = aug_ed.read_augments()["escape_rise"]
        if rise:
            aug_ed.action_aug_set_escapes(2)
            state2 = aug_ed.read_augments()
            expected = max(0, 15 + 2 * rise - int(state2["kills"] or 0) * state2["kill_drop"])
            check("改造体: 按放跑次数反推存在感", state2["presence"] == expected,
                  f"{state2['presence']} vs {expected} (rise {rise})")
            check("改造体: 反推出来的次数对得上", abs(float(state2["escapes"]) - 2) < 0.011,
                  str(state2["escapes"]))

        expect_presence = aug_ed.read_augments()["presence"]
        aug_ed.save(allow_running_game=True)
        aug_reload = savegame.SlotEditor(profile, sid).read_augments()
        check("改造体: 写盘后值保持",
              aug_reload["presence"] == expect_presence and aug_reload["next_day"] == -1,
              f"presence {aug_reload['presence']} (want {expect_presence}), next {aug_reload['next_day']}")

        # --- 初始特性（starting perks）---------------------------------
        perk_ed = savegame.SlotEditor(profile, sid)
        perk_catalog = perks.load_catalog()
        check("初始特性: 解包出的特性表", len(perk_catalog) >= 40, f"{len(perk_catalog)} 项")
        perk_node = perk_ed.node(savegame.P + ("startingPerks",))
        original_ids = [json.loads(perk_ed.doc.slice(e))["id"] for e in perk_node.items]
        check("初始特性: 读到自己拥有的特性", len(original_ids) > 0, str(original_ids[:4]))

        check("初始特性: 互斥能识别",
              bool(perks.conflicts(["lucky", "unlucky"], perk_catalog)),
              str(perks.conflicts(["lucky", "unlucky"], perk_catalog)))

        wanted = sorted(set(original_ids) | {"saving", "handy"})
        perk_ed.stage(
            savegame.P + ("startingPerks",),
            perks.build_entries(wanted, perk_catalog),
            "初始特性",
        )
        perk_ed.save(allow_running_game=True)
        perk_after = savegame.SlotEditor(profile, sid)
        ids_after = [json.loads(perk_after.doc.slice(e))["id"]
                     for e in perk_after.node(savegame.P + ("startingPerks",)).items]
        check("初始特性: 新增特性写回", set(ids_after) == set(wanted),
              f"{len(ids_after)} 项")
        kinds_ok = True
        for entry in perk_after.node(savegame.P + ("startingPerks",)).items:
            data = json.loads(perk_after.doc.slice(entry))
            if set(data) != {"id", "cost", "maxSlot", "incompatiblePerks", "type",
                             "mindEditable", "rawName", "rawDescription"}:
                kinds_ok = False
        check("初始特性: 字段结构与游戏一致", kinds_ok)

        # --- 牲畜（老鼠）遗传 ------------------------------------------
        animal_ed = savegame.SlotEditor(profile, sid)
        animal_index = None
        for i, it in enumerate(animal_ed.inventory_items(inv_key)):
            if animal_dna.is_animal(it["node"]):
                animal_index = i
                break
        if animal_index is not None:
            before_animal = animal_dna.read_animal(
                animal_ed.inventory_items(inv_key)[animal_index]["node"]
            )
            check("牲畜基因: 读到等位基因", len(before_animal["genes"]) >= 5,
                  f"{len(before_animal['genes'])} 条基因")
            check("牲畜基因: 表达值 = (A+B)/2",
                  all(abs(g["expressed"] - (g["a"] + g["b"]) / 2) < 1e-3 for g in before_animal["genes"]),
                  str([(g["label"], g["a"], g["b"], g["expressed"]) for g in before_animal["genes"]][:2]))
            check("牲畜基因: 物种与性别",
                  before_animal["species"].startswith("ANIMAL_"),
                  before_animal["species"])

            genes = {g["id"]: {"a": 42.0, "b": 8.0,
                               "partner_a": g["partner_a"], "partner_b": g["partner_b"]}
                     for g in before_animal["genes"]}
            stats = dict(before_animal["stats"])
            stats["ANIMAL_SEXE_TAG"] = "FEMALE"
            animal_ed.inventory_update_item(
                inv_key, animal_index,
                lambda doc, node: animal_dna.apply_animal(doc, node, genes, stats),
                label="牲畜基因",
            )
            animal_ed.save(allow_running_game=True)
            after_animal = savegame.SlotEditor(profile, sid)
            node_animal = after_animal.inventory_items(inv_key)[animal_index]["node"]
            info_animal = animal_dna.read_animal(node_animal)
            check("牲畜基因: 等位基因写回",
                  all(abs(g["a"] - 42.0) < 1e-3 and abs(g["b"] - 8.0) < 1e-3 for g in info_animal["genes"]),
                  str([(g["label"], g["a"], g["b"]) for g in info_animal["genes"]][:2]))
            check("牲畜基因: 表达值跟着重算",
                  all(abs(g["expressed"] - 25.0) < 1e-3 for g in info_animal["genes"]),
                  str(info_animal["genes"][0]["expressed"]))
            check("牲畜基因: 标签与键对齐",
                  len(node_animal.get("_keys").items) == len(node_animal.get("_values").items))

        # --- 游戏工厂数据 / 按默认构建 --------------------------------
        factory = itemcatalog.load_factory()
        check("工厂数据: 解包出的物品构建表", len(factory) > 300, f"{len(factory)} 件")
        spec = factory.get("water_filter_adv")
        check("工厂数据: 认得出高级滤水器的构建方式",
              bool(spec) and spec.get("durability") == 400 and spec.get("value") == 350,
              str(spec)[:80] if spec else "missing")

        cat_entry = itemcatalog.Catalog().get("water_filter_adv")
        if cat_entry and spec:
            cleaned = itemcatalog.defaultize_template(cat_entry.template, spec)
            parsed = json.loads(cleaned)
            names = [v["<identifier>k__BackingField"] for v in parsed.get("_values", [])]
            check("工厂数据: 默认构建去掉世界状态",
                  "BOUGHT_PRICE_TAG" not in names and "STOLEN_TAG" not in names,
                  str([n for n in names if "STOLEN" in n or "BOUGHT" in n]))
            check("工厂数据: 估值取工厂默认", parsed.get("unitValue") == 350, str(parsed.get("unitValue")))
            check("工厂数据: 类型取工厂默认",
                  set(parsed.get("itemTypes") or []) == set(spec.get("types") or []),
                  str(parsed.get("itemTypes")))

        # --- 鉴定（appraisal）------------------------------------------
        appr = savegame.SlotEditor(profile, sid)
        st_appr = appr.read_appraisal()
        check("鉴定: 读到等级与概率", st_appr["level"] is not None and st_appr["chance"] > 0,
              str({k: st_appr[k] for k in ('level', 'chance', 'days_missed', 'certs')}))
        check("鉴定: 证书检测", isinstance(st_appr["certs"], list), str(st_appr["certs"]))

        appr.action_appraisal_force_today()
        check("鉴定: 必来 = 100%", appr.read_appraisal()["chance"] == 100,
              str(appr.read_appraisal()["days_missed"]))

        appr.action_appraisal_reset_missed()
        appr.action_appraisal_set_level(1)
        st_lvl = appr.read_appraisal()
        check("鉴定: 等级可改", st_lvl["level"] == 1 and st_lvl["cert_id"] == "cert2",
              f"level {st_lvl['level']} cert {st_lvl['cert_id']}")

        appr.save(allow_running_game=True)
        st_disk = savegame.SlotEditor(profile, sid).read_appraisal()
        check("鉴定: 写盘后等级保持", st_disk["level"] == 1, str(st_disk["level"]))

        # --- crime & evidence fields are editable ----------------------
        crime = savegame.SlotEditor(profile, sid)
        sec = crime.node(savegame.P + ("secData",))
        crime.stage(savegame.P + ("secData", "evidenceLevel"), 0, "证据等级")
        amounts = sec.get("crimeAmount")
        totals = sec.get("crimeAmountTotal")
        for i in range(len(amounts)):
            crime.stage(savegame.P + ("secData", "crimeAmount", i), 0, f"犯罪值{i}")
        for i in range(len(totals)):
            crime.stage(savegame.P + ("secData", "crimeAmountTotal", i), 0, f"累计犯罪值{i}")
        crime.stage(savegame.P + ("secData", "assignedCaseOfficer"), "None", "负责警员")
        crime.save(allow_running_game=True)

        healed_crime = savegame.SlotEditor(profile, sid)
        sec_after = healed_crime.node(savegame.P + ("secData",))
        check("犯罪证据: 证据等级可清零",
              sec_after.get("evidenceLevel").value == 0,
              str(sec_after.get("evidenceLevel").value))
        check("犯罪证据: 犯罪值可清零",
              all(v.value == 0 for v in sec_after.get("crimeAmount").items)
              and all(v.value == 0 for v in sec_after.get("crimeAmountTotal").items),
              str(healed_crime.value(savegame.P + ("secData", "crimeAmount"))))
        check("犯罪证据: 案件警员字段可写",
              sec_after.get("assignedCaseOfficer").value == "None",
              repr(sec_after.get("assignedCaseOfficer").value))

        # one-click "clear crime" must also clear the wanted list and note
        nuke = savegame.SlotEditor(profile, sid)
        nuke.action_clear_crime()
        nuke.save(allow_running_game=True)
        nuked = savegame.SlotEditor(profile, sid)
        try:
            wanted = nuked.node(savegame.P + ("wantedStatus",))
        except KeyError:
            wanted = None
        check("犯罪证据: 一键清除覆盖通缉名单",
              wanted is None or wanted.kind != "object" or len(wanted) == 0,
              str(len(wanted)) if wanted is not None else "n/a")

        # untouched files must still be byte identical
        for name, data in others_before.items():
            if name == f"save_{sid}.es3":
                continue
            # the index file is allowed to change (preview sync); other slots are not
            if name != "saves_index.es3":
                check(f"物品编辑: {name} 未被动过",
                      es3json.load_file(str(profile_dir / name)) == data)

    # --- one-click actions must also produce a loadable file --------------
    if slots:
        sid = slots[0].slot_id
        ed = savegame.SlotEditor(profile, sid)
        ed.action_add_cash(50_000)
        ed.action_max_factions()
        ed.action_buy_all_upgrades(2)
        ed.action_unlock_services()
        ed.action_clear_crime()
        ed.action_clear_wounds()
        ed.action_delay_rent(120)
        ed.action_unlock_features()
        ed.action_multiply_item_values(3.0)
        try:
            res = ed.save(allow_running_game=True)
            check("一键作弊: 写盘成功", res.get("saved") is True, f"{len(res.get('changes', []))} 处修改")
        except savegame.EditError as exc:
            check("一键作弊: 写盘成功", False, str(exc))

        # file still parses and keeps its top-level shape
        doc = es3json.Document.load(str(profile.slot_path(sid)))
        check("一键作弊: 结构完整", (set(doc.root.keys_list()) == {"playerStore", "storeStation"}))
        reloaded = savegame.SlotEditor(profile, sid)
        reps = reloaded.node(savegame.P + ("storeReputations",))
        amounts = [r["amount"].value for r in reps.items]
        check("一键作弊: 声望全满", all(a >= 99.9 for a in amounts), str(amounts))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
