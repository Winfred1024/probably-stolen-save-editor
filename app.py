"""
存档修改器 for "深空当铺：可能是偷的" (Probably Stolen).

A small tkinter GUI that reads the game's Easy Save 3 files directly from
%LOCALAPPDATA%Low\\Questing Goose Studio\\Probably Stolen, lets you stage
changes, and writes them back with a backup and a self-check.

    python app.py                # normal use
    python app.py "<save dir>"   # point at a different profile folder
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import animal_dna
import inventory
import itemcatalog
import itemeditor
import itemprops
import module_editor
import perks
import presets as presets_mod
import seed_dna
import savegame
from savegame import BOOL, FLOAT, INT, STR, Field, Group

APP_TITLE = "深空当铺：可能是偷的 · 存档修改器"
ACCENT = "#2f6f4f"
WARN = "#a33a2a"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def fmt_number(value) -> str:
    """Compact but lossless-ish display for an entry box."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def human_count(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------------------- #
# main window
# --------------------------------------------------------------------------- #


class App:
    def __init__(self, root: tk.Tk, save_dir: str | Path | None = None):
        self.root = root
        self.profile: savegame.Profile | None = None
        self.catalog = itemcatalog.Catalog()
        self._factory = itemcatalog.load_factory()
        self.presets = presets_mod.PresetStore()
        self._catalog_rows: list[str] = []
        self.ed: savegame.SlotEditor | None = None
        self.slot_id: int | None = None
        self._fields: dict[tuple, tuple[Field, object, str]] = {}   # path -> (spec, var, initial text)
        self._initial_index: str = ""
        self._inv_key: str | None = None

        root.title(APP_TITLE)
        self._size_window()
        self._init_style()

        self._build_top()
        self._build_body()
        self._build_bottom()

        start = save_dir or savegame.discover_save_dir()
        if start:
            self.save_dir_var.set(str(start))
            self.reload_profile()
        else:
            self.set_status("没有自动找到存档目录，请点「浏览」选择。", warn=True)


    def _size_window(self) -> None:
        """Pick a sensible size and centre it on the primary monitor.

        Tk reports the whole virtual desktop, which on a multi-monitor setup
        would centre the window across the bezel -- so ask Windows for the
        primary work area instead.
        """
        left, top, avail_w, avail_h = self._primary_work_area()
        width = min(1220, max(920, avail_w - 80))
        height = min(830, max(600, avail_h - 80))
        x = left + max(0, (avail_w - width) // 2)
        y = top + max(0, (avail_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(980, width), min(620, height))

    @staticmethod
    def _primary_work_area() -> tuple[int, int, int, int]:
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

            rect = RECT()
            # SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
        return 0, 0, 1280, 720

    # -- styling -------------------------------------------------------------

    def _init_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        base = "Microsoft YaHei UI"
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                import tkinter.font as tkfont

                tkfont.nametofont(name).configure(family=base, size=10)
            except Exception:
                pass
        style.configure("Title.TLabel", font=(base, 13, "bold"))
        style.configure("Sub.TLabel", foreground="#666666")
        style.configure("H1.TLabel", font=(base, 11, "bold"))
        style.configure("Accent.TButton", font=(base, 10, "bold"))
        style.configure("Warn.TLabel", foreground=WARN)
        style.configure("Good.TLabel", foreground=ACCENT)
        style.configure("Treeview", rowheight=24)
        style.configure("Card.TFrame", background="#f6f6f4")
        # eleven tabs have to fit on one row
        style.configure("TNotebook.Tab", padding=(8, 4))

    # -- layout: top bar -----------------------------------------------------

    def _build_top(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="存档目录", style="H1.TLabel").pack(side="left")
        self.save_dir_var = tk.StringVar()
        self.dir_entry = ttk.Entry(bar, textvariable=self.save_dir_var)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(10, 6))
        ttk.Button(bar, text="浏览…", command=self.browse_dir).pack(side="left")
        ttk.Button(bar, text="重新扫描", command=self.reload_profile).pack(side="left", padx=(6, 0))
        self.open_dir_btn = ttk.Button(bar, text="打开文件夹", command=self.open_folder)
        self.open_dir_btn.pack(side="left", padx=(6, 0))

        self.header_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.header_var, style="Sub.TLabel", padding=(14, 0, 14, 6)).pack(
            fill="x"
        )


    # -- layout: body --------------------------------------------------------

    def _build_body(self) -> None:
        body = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        body.pack(fill="both", expand=True)
        self.body = body
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, width=312)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        left.grid_propagate(False)
        left.rowconfigure(1, weight=1)

        ttk.Label(left, text="存档槽位", style="H1.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))

        self.slot_tree = ttk.Treeview(left, columns=("title",), show="tree", selectmode="browse")
        self.slot_tree.column("#0", width=290)
        self.slot_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.slot_tree.yview)
        scroll.grid(row=1, column=0, sticky="nse", pady=0)
        self.slot_tree.configure(yscrollcommand=scroll.set)
        self.slot_tree.bind("<<TreeviewSelect>>", self._on_slot_select)

        self.slot_note_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.slot_note_var, style="Sub.TLabel", wraplength=290, justify="left").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )

        self.nb = ttk.Notebook(body)
        self.nb.grid(row=0, column=1, sticky="nsew")
        self._placeholder()

    def _placeholder(self) -> None:
        frame = ttk.Frame(self.nb, padding=30)
        ttk.Label(
            frame,
            text="← 先在左边选一个存档槽位",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "修改器会直接读写游戏存档（$LOCALAPPDATA%Low\\Questing Goose Studio\\Probably Stolen）。\n\n"
                "· 所有改动先「暂存」，点右下角「保存到游戏」才写盘\n"
                "· 写盘前自动备份到 _editor_backups 文件夹\n"
                "· 写盘后会重新读一遍，确认只有你要改的字段变了\n"
                "· 读档界面的「天数 / 现金」预览会一起同步"
            ),
            style="Sub.TLabel",
            justify="left",
            wraplength=760,
        ).pack(anchor="w", pady=(12, 0))
        self.nb.add(frame, text="说明")

    def _build_bottom(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 8, 12, 10))
        bar.pack(fill="x")

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(bar, textvariable=self.status_var, style="Sub.TLabel")
        self.status_label.pack(side="left")

        self.save_btn = ttk.Button(bar, text="保存到游戏", style="Accent.TButton", command=self.do_save)
        self.save_btn.pack(side="right")
        self.discard_btn = ttk.Button(bar, text="放弃修改", command=self.discard)
        self.discard_btn.pack(side="right", padx=(0, 8))
        self.pending_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.pending_var, style="Good.TLabel").pack(side="right", padx=(0, 16))
        # one static line, no detection code: the game must be closed before writing
        ttk.Label(bar, text="保存前请先退出游戏", style="Warn.TLabel").pack(side="right", padx=(0, 18))

        self._set_actions_enabled(False)

    # -- profile handling ----------------------------------------------------

    def browse_dir(self) -> None:
        start = self.save_dir_var.get() or str(Path.home())
        chosen = filedialog.askdirectory(title="选择 Probably Stolen 存档目录", initialdir=start)
        if chosen:
            self.save_dir_var.set(chosen)
            self.reload_profile()

    def open_folder(self) -> None:
        folder = self.save_dir_var.get()
        if folder and Path(folder).is_dir():
            os.startfile(folder)  # noqa: S606 - intended shell-open
        else:
            messagebox.showwarning("提示", "存档目录不存在。")

    def reload_profile(self) -> None:
        folder = self.save_dir_var.get().strip().strip('"')
        if not folder or not Path(folder).is_dir():
            self.set_status("存档目录不存在。", warn=True)
            return
        if not any(Path(folder).glob("*.es3")):
            self.set_status("这个目录里没有 .es3 存档文件。", warn=True)
            return

        self.profile = savegame.Profile(folder)
        self.ed = None
        self.slot_id = None
        self._set_actions_enabled(False)
        self._refresh_slot_list()
        self._refresh_catalog()

        play = self.profile.total_play_time()
        bits = [f"共 {len([s for s in self.profile.slots() if s.exists])} 个存档槽位"]
        if play:
            bits.append(f"总游戏时长 {play}")
        bits.append("改之前请先退出游戏")
        self.header_var.set("  ·  ".join(bits))
        self.set_status(f"已载入存档目录：{folder}")

    def _refresh_slot_list(self) -> None:
        assert self.profile is not None
        self.slot_tree.delete(*self.slot_tree.get_children())
        slots = self.profile.slots()
        if not slots:
            self.slot_note_var.set("没找到 save_N.es3 存档。")
            return
        for info in slots:
            node = self.slot_tree.insert("", "end", iid=str(info.slot_id), text=info.title())
            if not info.exists:
                self.slot_tree.item(node, tags=("empty",))
        self.slot_tree.tag_configure("empty", foreground="#999999")
        self.slot_note_var.set(
            "槽位标题来自存档里的读档预览（天数 / 现金）。\n"
            "带 day6/day7 的自动历史快照不会列出来，但也不会被改动。"
        )

    def _on_slot_select(self, _event=None) -> None:
        selection = self.slot_tree.selection()
        if not selection:
            return
        sid = int(selection[0])
        if self.ed is not None and self.ed.count_changes() > 0:
            if not messagebox.askyesno(
                "还有未保存的修改",
                f"槽位 {self.slot_id} 上有 {self.ed.count_changes()} 处修改还没保存。\n\n"
                "切换槽位会丢弃这些修改，继续吗？",
            ):
                self.slot_tree.selection_set(str(self.slot_id))
                return
        self.load_slot(sid)

    def load_slot(self, sid: int) -> None:
        assert self.profile is not None
        try:
            self.ed = savegame.SlotEditor(self.profile, sid)
        except Exception as exc:
            messagebox.showerror("打开存档失败", str(exc))
            self.set_status(f"打开槽位 {sid} 失败：{exc}", warn=True)
            return
        self.slot_id = sid
        self._inv_key = None
        self._fields.clear()
        self._rebuild_tabs()
        self._set_actions_enabled(True)
        self._update_pending()
        info = next((s for s in self.profile.slots() if s.slot_id == sid), None)
        self.set_status(f"已打开槽位 {sid}：{info.subtitle() if info else ''}")

    # -- tab construction ----------------------------------------------------

    def _rebuild_tabs(self) -> None:
        for tab in self.nb.tabs():
            self.nb.forget(tab)
        assert self.ed is not None
        self._build_fields_tab("金钱", savegame.MONEY_GROUP)
        self._build_fields_tab("车站", savegame.STATION_GROUP)
        self._build_fields_tab("健康", savegame.CHARACTER_GROUP)
        self._perk_catalog = perks.load_catalog()
        try:
            self._perk_original = [
                entry["id"] for entry in (json.loads(self.ed.doc.slice(e))
                                          for e in self.ed.node(savegame.P + ("startingPerks",)).items)
            ]
        except Exception:
            self._perk_original = []
        self._build_crime_tab()
        self._build_perk_tab()
        self._build_augment_tab()
        self._build_appraisal_tab()
        self._build_rat_tab()
        self._build_faction_tab()
        self._build_inventory_tab()
        self._build_catalog_tab()
        self._build_presets_tab()
        self._build_unlock_tab()
        self._build_backup_tab()
        self.nb.select(0)

    # -- generic field tabs --------------------------------------------------

    def _build_fields_tab(self, title: str, group: Group) -> None:
        """Build a tab out of a Group of Field specs."""
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text=title)

        if group.blurb:
            ttk.Label(outer, text=group.blurb, style="Sub.TLabel", wraplength=780, justify="left").pack(
                anchor="w", pady=(0, 10)
            )

        # footer first (bottom-up) so the field area keeps the slack
        self._add_presets(outer, title)

        hint_var = tk.StringVar(value="鼠标点到某个输入框，这里会显示它的说明。")
        ttk.Label(outer, textvariable=hint_var, style="Sub.TLabel", anchor="w").pack(
            side="bottom", fill="x", pady=(8, 0)
        )

        grid = ttk.Frame(outer)
        grid.pack(fill="both", expand=True, anchor="nw")

        per_col = (len(group.fields) + 1) // 2
        for i, spec in enumerate(group.fields):
            col = i // per_col
            row = i % per_col
            cell = ttk.Frame(grid, padding=(0, 6, 18, 6))
            cell.grid(row=row, column=col, sticky="w")

            ttk.Label(cell, text=spec.label, width=16, anchor="w").pack(side="left")

            try:
                current = self.ed.read(spec.path)
            except (KeyError, IndexError, TypeError):
                ttk.Label(cell, text="（这个存档里没有该字段）", style="Sub.TLabel").pack(side="left")
                continue

            if spec.kind == BOOL:
                var = tk.BooleanVar(value=bool(current))
                widget = ttk.Checkbutton(cell, variable=var, text="启用")
                widget.pack(side="left")
                initial = "True" if current else "False"
            else:
                initial = fmt_number(current)
                var = tk.StringVar(value=initial)
                widget = ttk.Entry(cell, textvariable=var, width=12, justify="right")
                widget.pack(side="left")

            widget.bind("<FocusIn>", lambda _e, s=spec: hint_var.set(s.hint or s.label))
            var.trace_add("write", lambda *_a: self._update_pending())

            self._fields[spec.path] = (spec, var, initial)

    def _add_presets(self, parent: ttk.Frame, tab_title: str) -> None:
        """A few one-click conveniences, only on the money/stations tabs."""
        assert self.ed is not None
        if "金钱" in tab_title:
            bar = ttk.LabelFrame(parent, text=" 快捷操作 ", padding=(12, 8))
            bar.pack(fill="x", side="bottom", pady=(14, 0))
            for text, amount in (("现金 +10,000", 10_000), ("现金 +100,000", 100_000)):
                ttk.Button(
                    bar, text=text, command=lambda a=amount: self._quick_cash_add(a)
                ).pack(side="left", padx=(0, 8))
            ttk.Button(bar, text="现金设为 999,999", command=lambda: self._quick_cash_set(999_999)).pack(
                side="left", padx=(0, 8)
            )
            ttk.Button(bar, text="租金 → 99 天", command=self._quick_delay_rent).pack(side="left")

        elif "车站" in tab_title:
            bar = ttk.LabelFrame(parent, text=" 快捷操作 ", padding=(12, 8))
            bar.pack(fill="x", side="bottom", pady=(14, 0))
            ttk.Button(bar, text="客流 / 满意度 / 秩序 → 100", command=self._quick_max_station).pack(
                side="left", padx=(0, 8)
            )
            ttk.Button(bar, text="水质 → 10000", command=self._quick_max_water).pack(side="left", padx=(0, 8))
            ttk.Button(bar, text="势力关系 → 100", command=self._quick_max_powers).pack(side="left")

    # -- 牲畜（老鼠）总览 -------------------------------------------------

    def _build_rat_tab(self) -> None:
        assert self.ed is not None
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="老鼠")
        outer = ttk.Frame(shell, padding=(14, 10))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        self.rat_summary = ttk.Label(header, text="", style="Title.TLabel")
        self.rat_summary.pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "这里列出存档里所有牲畜（老鼠），含它们在哪个背包、哪个笼子里。"
                "选中一只再点「用它覆盖全部老鼠」，就能把这套基因铺满整个存档；也可以多选后批量纯合化 / 拉满 / 恢复基础值。"
            ),
            style="Sub.TLabel",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(8, 2))
        ttk.Button(bar, text="刷新", command=self._refresh_rats).pack(side="left")
        ttk.Button(bar, text="全选", command=lambda: self._select_all_rats(True)).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="全不选", command=lambda: self._select_all_rats(False)).pack(side="left", padx=(8, 0))

        bar2 = ttk.Frame(outer)
        bar2.pack(fill="x", pady=(2, 6))
        ttk.Button(bar2, text="纯合化（A=B）", command=self._rat_homozygous).pack(side="left")
        ttk.Button(bar2, text="全部拉满", command=self._rat_max).pack(side="left", padx=(8, 0))
        ttk.Button(bar2, text="恢复基础值", command=self._rat_base).pack(side="left", padx=(8, 0))
        ttk.Button(bar2, text="用它覆盖全部老鼠", command=self._rat_apply_all).pack(side="left", padx=(8, 0))
        ttk.Label(bar2, text="（多选后可批量）", style="Sub.TLabel").pack(side="left", padx=(8, 0))

        columns = ("where", "sex", "age", "weight", "hp", "hunger", "fertility",
                   "growth", "litter", "immunity", "maxhp", "longevity")
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True)
        self.rat_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="extended")
        for col, text, width, anchor in (
            ("where", "位置", 230, "w"),
            ("sex", "性别", 40, "center"),
            ("age", "年龄", 40, "e"),
            ("weight", "体重", 45, "e"),
            ("hp", "生命", 50, "e"),
            ("hunger", "饱食", 48, "e"),
            ("fertility", "生育", 42, "e"),
            ("growth", "生长", 45, "e"),
            ("litter", "每胎", 45, "e"),
            ("immunity", "免疫", 45, "e"),
            ("maxhp", "血上限", 55, "e"),
            ("longevity", "寿命", 45, "e"),
        ):
            self.rat_tree.heading(col, text=text)
            self.rat_tree.column(col, width=width, anchor=anchor)
        self.rat_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.rat_tree.yview)
        scroll.pack(side="right", fill="y")
        self.rat_tree.configure(yscrollcommand=scroll.set)
        self.rat_tree.bind("<Double-1>", lambda _e: self._open_rat_editor())

        self.rat_detail = ttk.Label(outer, text="", style="Sub.TLabel", wraplength=900, justify="left")
        self.rat_detail.pack(anchor="w", pady=(8, 0))
        self.rat_tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_rat_detail())

        self._rat_rows: list[dict] = []
        self._refresh_rats()

    # -- data -----------------------------------------------------------

    def _rat_rows_for(self) -> list[dict]:
        rows = []
        if self.ed is None:
            return rows
        for key, label, _count in self.ed.inventories():
            try:
                inv = inventory.Inventory(self.ed.inventory(key))
            except Exception:
                continue
            parent = {}
            names = {}
            for item in inv.items():
                names[item.uuid] = item.name or item.identifier
                for child in inventory._child_list(item.node):
                    parent[child] = item.uuid
            for item in inv.items():
                if not animal_dna.is_animal(item.node):
                    continue
                chain = []
                cursor = item.uuid
                guard = 0
                while cursor in parent and guard < 32:
                    cursor = parent[cursor]
                    guard += 1
                    if cursor != 0 and cursor in names:
                        chain.append(names[cursor])
                parts = list(reversed(chain))
                if len(parts) > 2:
                    parts = parts[-2:]
                where = label + (">" + ">".join(parts) if parts else "")
                rows.append({"key": key, "label": label, "where": where,
                             "index": item.uuid, "item": item})
        return rows

    def _refresh_rats(self) -> None:
        if self.ed is None or not hasattr(self, "rat_tree"):
            return
        rows = self._rat_rows_for()
        rows.sort(key=lambda r: (r["label"], r["index"]))
        self._rat_rows = rows
        self.rat_tree.delete(*self.rat_tree.get_children())
        for row in rows:
            info = animal_dna.read_animal(row["item"].node)
            stats = info["stats"]
            genes = {g["id"]: g for g in info["genes"]}

            def value(gene_id):
                gene = genes.get(gene_id)
                return f"{gene['expressed']:.2f}" if gene else ""

            sex = "公" if str(stats.get("ANIMAL_SEXE_TAG", "")).upper().startswith("M") else "母"
            self.rat_tree.insert(
                "", "end", iid=f"{row['key']}:{row['index']}",
                values=(
                    row["where"], sex,
                    stats.get("ANIMAL_AGE_TAG") or "",
                    f"{stats.get('ANIMAL_WEIGHT_TAG') or 0:.0f}",
                    f"{stats.get('ANIMAL_HEALTH_TAG') or 0:.0f}",
                    f"{stats.get('ANIMAL_HUNGER_TAG') or 0:.0f}",
                    f"{stats.get('ANIMAL_CURRENT_FERTILITY_TAG') or 0:.0f}",
                    value("ANIMAL_GROWTH_RATE_TAG"),
                    value("ANIMAL_LITTER_SIZE_TAG"),
                    value("ANIMAL_IMMUNITY_TAG"),
                    value("ANIMAL_MAX_HEALTH_TAG"),
                    value("ANIMAL_LONGEVITY_TAG"),
                ),
            )
        self.rat_summary.configure(text=f"牲畜总览 · 共 {len(rows)} 只")
        self.rat_detail.configure(text="选中一只可以看它的完整等位基因；双击直接打开编辑器。")

    def _refresh_rat_detail(self) -> None:
        selection = self.rat_tree.selection()
        if len(selection) != 1:
            return
        row = self._row_by_iid(selection[0])
        if row is None:
            return
        info = animal_dna.read_animal(row["item"].node)
        parts = [f"{g['label']} A={g['a']:.2f}/B={g['b']:.2f}={g['expressed']:.2f}"
                 for g in info["genes"]]
        self.rat_detail.configure(text=f"{row['where']}： " + " · ".join(parts))

    def _row_by_iid(self, iid: str) -> dict | None:
        for row in self._rat_rows:
            if f"{row['key']}:{row['index']}" == iid:
                return row
        return None

    def _select_all_rats(self, select: bool) -> None:
        children = self.rat_tree.get_children()
        if select:
            self.rat_tree.selection_set(children)
        else:
            self.rat_tree.selection_remove(children)

    def _open_rat_editor(self) -> None:
        selection = self.rat_tree.selection()
        if len(selection) != 1:
            return
        row = self._row_by_iid(selection[0])
        if row is None or self.ed is None:
            return
        existing = getattr(self, "_item_editor", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.force_focus() if hasattr(existing, "force_focus") else existing.focus_force()
            return
        self._inv_key = row["key"]
        try:
            dialog = itemeditor.ItemEditor(
                self.root, self.ed, row["key"], row["index"],
                on_saved=self._after_item_edit, initial_tab="牲畜基因",
            )
        except Exception as exc:
            messagebox.showerror("打不开属性编辑器", str(exc))
            return
        self._item_editor = dialog

    # -- batch gene edits -------------------------------------------------

    def _selected_rat_rows(self) -> list[dict]:
        out = []
        for iid in self.rat_tree.selection():
            row = self._row_by_iid(iid)
            if row is not None:
                out.append(row)
        return out

    def _apply_to_rats(self, rows: list[dict], mutation) -> int:
        """`mutation(genes_dict, stats)` returns the genes to write."""
        if self.ed is None or not rows:
            return 0
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        by_bag: dict[str, list] = {}
        for row in rows:
            info = animal_dna.read_animal(row["item"].node)
            current = {g["id"]: {"a": g["a"], "b": g["b"],
                                 "partner_a": g["partner_a"], "partner_b": g["partner_b"]}
                       for g in info["genes"]}
            genes = mutation(current, info["stats"])
            if genes is None:
                continue
            stats = dict(info["stats"])
            by_bag.setdefault(row["key"], []).append(
                (row["index"],
                 lambda doc, node, genes=genes, stats=stats: animal_dna.apply_animal(
                     doc, node, genes, stats
                 ))
            )

        done = 0
        for key, updates in by_bag.items():
            try:
                done += self.ed.inventory_update_many(key, updates, "牲畜基因")
            except Exception as exc:
                messagebox.showerror("修改失败", str(exc))
        try:
            self.root.configure(cursor="")
        except Exception:
            pass
        if done:
            self._refresh_rats()
            self._update_pending()
            self.set_status(f"已暂存 {done} 只老鼠的基因修改，记得保存")
        return done

    def _rat_homozygous(self) -> None:
        def mutation(current, _stats):
            return {gid: {"a": (g["a"] + g["b"]) / 2, "b": (g["a"] + g["b"]) / 2,
                          "partner_a": g["partner_a"], "partner_b": g["partner_b"]}
                    for gid, g in current.items()}

        self._apply_to_rats(self._selected_rat_rows(), mutation)

    def _rat_max(self) -> None:
        def mutation(current, _stats):
            return {gid: {"a": 100.0, "b": 100.0,
                          "partner_a": g["partner_a"], "partner_b": g["partner_b"]}
                    for gid, g in current.items()}

        self._apply_to_rats(self._selected_rat_rows(), mutation)

    def _rat_base(self) -> None:
        def mutation(current, _stats):
            out = {}
            for gid, g in current.items():
                base = animal_dna.GENES.get(gid, ("", 0.0, ""))[1]
                out[gid] = {"a": base, "b": base,
                            "partner_a": g["partner_a"], "partner_b": g["partner_b"]}
            return out

        self._apply_to_rats(self._selected_rat_rows(), mutation)

    def _rat_apply_all(self) -> None:
        """One selected rat becomes the template for every other rat in the save."""
        rows = self._selected_rat_rows()
        if len(rows) != 1:
            messagebox.showinfo("提示", "先在列表里选中**一只**老鼠作为模板，再点这个按钮。")
            return
        source = rows[0]
        source_info = animal_dna.read_animal(source["item"].node)
        template = {
            g["id"]: {"a": g["a"], "b": g["b"],
                      "partner_a": g["partner_a"], "partner_b": g["partner_b"]}
            for g in source_info["genes"]
        }
        targets = [
            row for row in self._rat_rows
            if not (row["key"] == source["key"] and row["index"] == source["index"])
        ]
        if not targets:
            messagebox.showinfo("提示", "存档里只有这一只老鼠，没有可覆盖的对象。")
            return
        if not messagebox.askyesno(
            "确认覆盖",
            f"把「{source['where']}」这只的基因，覆盖到其余 {len(targets)} 只老鼠？\n\n"
            "只会改基因（等位基因 A/B），它们的性别、年龄、体力等保持原样。",
        ):
            return

        def mutation(_current, _stats):
            return {gid: dict(spec) for gid, spec in template.items()}

        self._apply_to_rats(targets, mutation)
        self.set_status(f"已用「{source['where']}」的基因覆盖 {len(targets)} 只老鼠，记得保存")

    # -- 初始特性 ---------------------------------------------------------

    def _build_perk_tab(self) -> None:
        assert self.ed is not None
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="特性")
        scroller = itemeditor.ScrollFrame(shell)
        scroller.pack(fill="both", expand=True)
        outer = ttk.Frame(scroller.inner, padding=(16, 12))
        outer.pack(fill="both", expand=True)

        titles = ttk.Frame(outer)
        titles.pack(fill="x")
        self.perk_summary = ttk.Label(titles, text="", style="Title.TLabel")
        self.perk_summary.pack(anchor="w")
        self.perk_note = ttk.Label(titles, text="", style="Sub.TLabel", wraplength=740, justify="left")
        self.perk_note.pack(anchor="w", pady=(2, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(8, 6))
        ttk.Button(buttons, text="只留正面", command=self._perk_positive_only).pack(side="left")
        ttk.Button(buttons, text="全选", command=self._perk_all).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="全不选", command=self._perk_none).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="恢复读取时", command=self._perk_reset).pack(side="left", padx=(8, 0))

        current = list(self._perk_original)
        self.perk_vars: dict[str, tk.BooleanVar] = {}
        catalog = self._perk_catalog
        ordered = sorted(
            catalog.values(),
            key=lambda e: (int(e.get("type", 0)), -int(e.get("cost", 0)), perks.label(e["id"])),
        )

        columns = 3
        box = ttk.LabelFrame(outer, text=" 初始特性（勾选 = 拥有） ", padding=(12, 10))
        box.pack(fill="x")
        for index, entry in enumerate(ordered):
            perk_id = entry["id"]
            cell = ttk.Frame(box)
            cell.grid(row=index // columns, column=index % columns, sticky="w", padx=(0, 16), pady=2)
            var = tk.BooleanVar(value=perk_id in current)
            var.trace_add("write", lambda *_a: self._refresh_perks())
            ttk.Checkbutton(cell, variable=var, text=perks.label(perk_id), width=12).pack(side="left")
            cost = int(entry.get("cost", 0))
            ttk.Label(
                cell,
                text=f"{cost:+d}",
                width=3,
                style="Sub.TLabel" if cost >= 0 else "Warn.TLabel",
            ).pack(side="left")
            hint = perks.DESCRIPTIONS.get(perk_id, "")
            ttk.Label(cell, text=hint[:18], style="Sub.TLabel", width=18).pack(side="left")
            self.perk_vars[perk_id] = var

        self._refresh_perks()

    def _selected_perk_ids(self) -> list[str]:
        return [pid for pid, var in getattr(self, "perk_vars", {}).items() if var.get()]

    def _refresh_perks(self) -> None:
        if not hasattr(self, "perk_summary"):
            return
        chosen = self._selected_perk_ids()
        total = sum(int(self._perk_catalog.get(pid, {}).get("cost", 0)) for pid in chosen)
        self.perk_summary.configure(text=f"初始特性 · 已选 {len(chosen)} 项 · 点数合计 {total:+d}")
        clashes = perks.conflicts(chosen, self._perk_catalog)
        if clashes:
            pairs = "、".join(f"{perks.label(a)} + {perks.label(b)}" for a, b in clashes[:3])
            self.perk_note.configure(
                text=f"⚠ 有互斥组合：{pairs}（游戏里这两项不能同时拥有，建议去掉一个）",
                style="Warn.TLabel",
            )
        else:
            self.perk_note.configure(
                text=(
                    "勾选即拥有。正面特性花点数，缺陷给点数；"
                    "「改造体威胁」「黄金地段」这类特殊项也在列表里。"
                ),
                style="Sub.TLabel",
            )

    def _perk_positive_only(self) -> None:
        for pid, var in self.perk_vars.items():
            entry = self._perk_catalog.get(pid, {})
            var.set(int(entry.get("cost", 0)) > 0 and int(entry.get("type", 0)) == 0)

    def _perk_all(self) -> None:
        for var in self.perk_vars.values():
            var.set(True)

    def _perk_none(self) -> None:
        for var in self.perk_vars.values():
            var.set(False)

    def _perk_reset(self) -> None:
        for pid, var in self.perk_vars.items():
            var.set(pid in self._perk_original)

    # -- crime & evidence ----------------------------------------------------

    CRIME_LABELS = {
        "FENCING": "销赃",
        "DRUG_TRAFFICKING": "贩毒",
        "CONTRABAND": "违禁品",
        "THEFT": "盗窃",
        "MURDER": "谋杀",
        "SMUGGLING": "走私",
    }

    TRACKER_LABELS = [
        ("dirtyWaterTracker", "脏水证据"),
        ("mislabeledWaterTracker", "贴错标签的水"),
        ("badChemTrackerFraud", "劣质化学品·欺诈"),
        ("badChemTrackerEndangerment", "劣质化学品·危害"),
        ("bribeTracker", "行贿记录"),
        ("badAmmoTracker", "劣质弹药"),
        ("badAmmoTrackerLegal", "劣质弹药·合法"),
        ("endangermentItemTracker", "危险物品"),
    ]

    def _build_crime_tab(self) -> None:
        assert self.ed is not None
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="犯罪")
        scroller = itemeditor.ScrollFrame(shell)
        scroller.pack(fill="both", expand=True)
        outer = ttk.Frame(scroller.inner, padding=(16, 12))
        outer.pack(fill="both", expand=True)

        sec = self.ed.node(savegame.P + ("secData",))
        evidence = self.ed.value(savegame.P + ("secData", "evidenceLevel"))
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(
            header,
            text=f"当前证据等级：{evidence}",
            style="Title.TLabel",
        ).pack(side="left")
        self.crime_note = ttk.Label(header, text="", style="Sub.TLabel")
        self.crime_note.pack(side="left", padx=(14, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(8, 4))
        ttk.Button(buttons, text="清除全部证据", command=self._crime_clear_all).pack(side="left")
        ttk.Button(buttons, text="证据等级 → 0", command=self._crime_zero_evidence).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="犯罪值 → 0", command=self._crime_zero_amounts).pack(side="left", padx=(8, 0))
        ttk.Label(
            buttons,
            text="证据等级越高，治安部越容易上门搜查", style="Sub.TLabel"
        ).pack(side="left", padx=(14, 0))

        # evidence level
        grid = ttk.Frame(outer)
        grid.pack(fill="x", pady=(8, 0))
        cell = ttk.Frame(grid)
        cell.grid(row=0, column=0, sticky="w")
        ttk.Label(cell, text="证据等级", width=12).pack(side="left")
        self._crime_entry(
            cell, Field("证据等级", savegame.P + ("secData", "evidenceLevel"), INT, 0, 99)
        )

        # per-crime amounts
        box = ttk.LabelFrame(outer, text=" 各罪名的犯罪值 ", padding=(12, 8))
        box.pack(fill="x", pady=(10, 0))
        types = [t.value for t in sec.get("crimeTypes").items]
        amount = sec.get("crimeAmount")
        total = sec.get("crimeAmountTotal")
        if types:
            head = ttk.Frame(box)
            head.pack(fill="x", pady=(0, 4))
            ttk.Label(head, text="罪名", width=24, style="H1.TLabel").pack(side="left")
            ttk.Label(head, text="当前犯罪值", width=15, style="H1.TLabel").pack(side="left")
            ttk.Label(head, text="累计犯罪值", width=15, style="H1.TLabel").pack(side="left", padx=(12, 0))
            for i, crime in enumerate(types):
                row = ttk.Frame(box)
                row.pack(fill="x", pady=3)
                label = self.CRIME_LABELS.get(crime, crime)
                ttk.Label(row, text=f"{label}（{crime}）", width=24).pack(side="left")
                if amount is not None and i < len(amount):
                    ttk.Label(row, text="当前", width=5).pack(side="left")
                    self._crime_entry(row, Field(f"{label}·当前犯罪值",
                                                 savegame.P + ("secData", "crimeAmount", i), INT, 0, 9999))
                if total is not None and i < len(total):
                    ttk.Label(row, text="累计", width=5).pack(side="left", padx=(12, 0))
                    self._crime_entry(row, Field(f"{label}·累计犯罪值",
                                                 savegame.P + ("secData", "crimeAmountTotal", i), INT, 0, 9999))
        else:
            ttk.Label(box, text="这个存档还没有任何犯罪记录。", style="Sub.TLabel").pack(anchor="w")

        # trackers
        tracker_box = ttk.LabelFrame(outer, text=" 证据追踪器 ", padding=(12, 8))
        tracker_box.pack(fill="x", pady=(10, 0))
        grid2 = ttk.Frame(tracker_box)
        grid2.pack(fill="x")
        for i, (key, label) in enumerate(self.TRACKER_LABELS):
            path = savegame.P + ("secData", key)
            try:
                self.ed.node(path)
            except KeyError:
                continue
            cell = ttk.Frame(grid2)
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 24), pady=3)
            ttk.Label(cell, text=label, width=18).pack(side="left")
            self._crime_entry(cell, Field(label, path, INT, 0, 9999))

        # case info
        case_box = ttk.LabelFrame(outer, text=" 案件信息 ", padding=(12, 8))
        case_box.pack(fill="x", pady=(10, 0))
        grid3 = ttk.Frame(case_box)
        grid3.pack(fill="x")
        for i, (key, label, kind, lo, hi) in enumerate((
            ("state", "案件状态", INT, 0, 99),
            ("recommendedAction", "建议处置", INT, 0, 99),
            ("assignedCaseOfficer", "负责警员", STR, None, None),
            ("note", "备注", STR, None, None),
            ("wrongfulReportCount", "误报次数", INT, 0, 9999),
            ("wrongfulReportMax", "误报上限", INT, 0, 9999),
        )):
            path = savegame.P + ("secData", key) if key != "wrongfulReportCount" and key != "wrongfulReportMax" \
                else savegame.P + (key,)
            try:
                self.ed.node(path)
            except KeyError:
                continue
            cell = ttk.Frame(grid3)
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 24), pady=3)
            ttk.Label(cell, text=label, width=12).pack(side="left")
            self._crime_entry(cell, Field(label, path, kind, lo, hi), width=22 if kind == STR else 10)

        try:
            wanted = self.ed.node(savegame.P + ("wantedStatus",))
        except KeyError:
            wanted = None
        count = len(wanted) if wanted is not None and wanted.kind == "object" else 0
        ttk.Label(
            outer,
            text=f"通缉名单：{count} 条" + ("（点「清除全部证据」会一并清空）" if count else "（空）"),
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        self._update_crime_note()

    def _crime_entry(self, parent, spec: Field, width: int = 10):
        value = self.ed.read(spec.path)
        if spec.kind == BOOL:
            var = tk.BooleanVar(value=bool(value))
            ttk.Checkbutton(parent, variable=var).pack(side="left")
        else:
            initial = fmt_number(value)
            var = tk.StringVar(value=initial)
            ttk.Entry(parent, textvariable=var, width=width, justify="right").pack(side="left")
        var.trace_add("write", lambda *_a: self._update_pending())
        initial_text = "True" if isinstance(value, bool) else ("" if value is None else str(value))
        self._fields[tuple(spec.path)] = (spec, var, initial_text)
        return var

    def _update_crime_note(self) -> None:
        if self.ed is None:
            return
        try:
            level = int(self.ed.read(savegame.P + ("secData", "evidenceLevel")))
        except Exception:
            return
        if level <= 0:
            text = "没有证据，治安部不会找你。"
        elif level <= 2:
            text = "有零星证据，暂时安全。"
        elif level <= 4:
            text = "证据已经成型，可能被突击检查。"
        else:
            text = "证据充分，随时可能被抓。"
        if hasattr(self, "crime_note"):
            self.crime_note.configure(text=text)

    def _crime_clear_all(self) -> None:
        self._quick(lambda e: e.action_clear_crime())
        self._update_crime_note()

    def _crime_zero_evidence(self) -> None:
        if self.ed is None:
            return
        self.ed.stage(savegame.P + ("secData", "evidenceLevel"), 0, "证据等级 = 0")
        self._sync_widgets_from_editor()
        self._update_pending()
        self._update_crime_note()

    def _crime_zero_amounts(self) -> None:
        if self.ed is None:
            return
        sec = self.ed.node(savegame.P + ("secData",))
        for field in ("crimeAmount", "crimeAmountTotal"):
            arr = sec.get(field, default=None)
            if arr is None:
                continue
            for i in range(len(arr)):
                self.ed.stage(savegame.P + ("secData", field, i), 0, f"{field}[{i}] = 0")
        self._sync_widgets_from_editor()
        self._update_pending()

    # -- 改造体 -----------------------------------------------------------

    def _build_augment_tab(self) -> None:
        assert self.ed is not None
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="改造")
        scroller = itemeditor.ScrollFrame(shell)
        scroller.pack(fill="both", expand=True)
        outer = ttk.Frame(scroller.inner, padding=(16, 12))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        titles = ttk.Frame(outer)
        titles.pack(fill="x")
        self.aug_summary = ttk.Label(titles, text="", style="Title.TLabel")
        self.aug_summary.pack(anchor="w")
        self.aug_stats = ttk.Label(titles, text="", style="H1.TLabel")
        self.aug_stats.pack(anchor="w", pady=(2, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 4))
        ttk.Button(buttons, text="立刻刷出改造体", style="Accent.TButton",
                   command=self._aug_spawn_now).pack(side="left")
        ttk.Button(buttons, text="推迟 7 天", command=self._aug_delay).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="存在感清零", command=self._aug_zero).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="结束这条线", command=self._aug_end).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="重开一条线", command=self._aug_restart).pack(side="left", padx=(8, 0))

        rules = ttk.LabelFrame(outer, text=" 游戏里的规则（从代码里读出来的） ", padding=(12, 8))
        rules.pack(fill="x", pady=(10, 0))
        ttk.Label(
            rules,
            text=(
                "· 第 12 天引入改造体（开局天赋「改造体威胁」则是第 1 天）。\n"
                "· 第 13 天、第 18 天各来一个「改造体医生」剧情客人。\n"
                "· 之后按「下次刷出日」来客：到日子还没处理就刷一个改造体，刷完自动 +6~8 天。\n"
                "· 存在感（augPresence）：放跑一个会涨，打死一个会降；第 62 天起且存在感 ≤ 0 时这条线收尾。"
            ),
            justify="left",
            wraplength=880,
        ).pack(anchor="w")

        grid = ttk.LabelFrame(outer, text=" 状态与数值 ", padding=(12, 10))
        grid.pack(fill="x", pady=(10, 0))

        row = ttk.Frame(grid)
        row.pack(fill="x", pady=(0, 6))
        self._aug_check(row, "已引入", savegame.P + ("isAugIntroduced",))
        self._aug_check(row, "已收尾", savegame.P + ("isAugEnded",))
        self._aug_check(row, "今天已处理过", savegame.P + ("storeClientManager", "isAugSpawnHandledToday"))

        values = ttk.Frame(grid)
        values.pack(fill="x")
        for label, path, width in (
            ("存在感", savegame.P + ("augPresence",), 8),
            ("下次刷出日", savegame.P + ("nextAugSpawnDay",), 8),
            ("已击杀", savegame.P + ("runStats", "augsShot"), 8),
        ):
            cell = ttk.Frame(values)
            cell.pack(side="left", padx=(0, 22))
            ttk.Label(cell, text=label, width=10).pack(side="left")
            self._crime_entry(cell, Field(label, path, INT, -1, 9999), width=width)

        derived = ttk.Frame(grid)
        derived.pack(fill="x", pady=(8, 0))
        ttk.Label(derived, text="按放跑次数写回", width=14).pack(side="left")
        self.aug_escape_var = tk.StringVar(value="0")
        ttk.Spinbox(derived, from_=0, to=99, textvariable=self.aug_escape_var, width=6).pack(side="left")
        ttk.Button(derived, text="写回存在感", command=self._aug_set_escapes).pack(side="left", padx=(8, 0))
        ttk.Label(
            derived,
            text="15 + 放跑×惩罚 − 击杀×扣减",
            style="Sub.TLabel",
        ).pack(side="left", padx=(12, 0))

        self.aug_note = ttk.Label(outer, text="", style="Sub.TLabel", wraplength=740, justify="left")
        self.aug_note.pack(anchor="w", pady=(10, 0))

        self._refresh_augments()

    def _aug_check(self, parent, label: str, path) -> None:
        cell = ttk.Frame(parent)
        cell.pack(side="left", padx=(0, 18))
        self._crime_entry(cell, Field(label, path, BOOL))
        ttk.Label(cell, text=label).pack(side="left")

    def _refresh_augments(self) -> None:
        if self.ed is None or not hasattr(self, "aug_summary"):
            return
        state = self.ed.read_augments()
        day = state["day"]
        next_day = state["next_day"]
        if next_day is None:
            when = "未知"
        elif int(next_day) < 0:
            when = "由游戏随机排"
        elif int(next_day) <= day:
            when = f"已到期（第 {next_day} 天）"
        else:
            when = f"第 {next_day} 天（还有 {int(next_day) - day} 天）"
        flags = ["已引入" if state["introduced"] else "未引入",
                 "已收尾" if state["ended"] else "进行中"]
        if state["handled"]:
            flags.append("今天已处理")
        escapes = state["escapes"]
        escape_text = "-" if escapes is None else (f"{escapes:g}" if isinstance(escapes, float) else str(escapes))
        self.aug_summary.configure(text=f"改造体 · {' · '.join(flags)}")
        self.aug_stats.configure(
            text=(
                f"存在感 {state['presence']} · 放跑 {escape_text} 次 · 击杀 {state['kills']}"
                f" · 下次刷出 {when}"
            )
        )
        rise = state["escape_rise"]
        drop = state["kill_drop"]
        self.aug_note.configure(
            text=(
                f"当前是第 {day} 天，难度 {state['difficulty'] or '未知'}：放跑一个 +{rise}，打死一个 -{drop}，"
                f"初始值 15（拿到霰弹枪时写入）。第 62 天（引入日 +50）起，存在感 ≤ 0 才会触发收尾。"
            )
        )
        if hasattr(self, "aug_escape_var") and escapes is not None:
            self.aug_escape_var.set(f"{escapes:g}" if isinstance(escapes, float) else str(escapes))

    def _after_aug_action(self) -> None:
        self._sync_aug_fields()
        self._refresh_augments()
        self._update_pending()

    def _sync_aug_fields(self) -> None:
        """Push staged augment values back into the entry widgets.

        Otherwise the (stale) entry text would be collected on save and undo the
        action we just staged.
        """
        if self.ed is None:
            return
        state = self.ed.read_augments()
        mapping = {
            savegame.P + ("isAugIntroduced",): state["introduced"],
            savegame.P + ("isAugEnded",): state["ended"],
            savegame.P + ("storeClientManager", "isAugSpawnHandledToday"): state["handled"],
            savegame.P + ("augPresence",): state["presence"],
            savegame.P + ("nextAugSpawnDay",): state["next_day"],
            savegame.P + ("runStats", "augsShot"): state["kills"],
        }
        for path, value in mapping.items():
            entry = self._fields.get(path)
            if not entry:
                continue
            spec, var, _initial = entry
            if spec.kind == BOOL:
                var.set(bool(value))
            else:
                var.set(fmt_number(value))
            # initial must stay the on-disk value, otherwise _collect() sees the
            # widget as untouched and unstages the action we just staged.
            original = self.ed.original_value(path)
            if isinstance(original, bool):
                initial = "True" if original else "False"
            elif original is None:
                initial = ""
            else:
                initial = str(original)
            self._fields[path] = (spec, var, initial)

    def _aug_spawn_now(self) -> None:
        if self.ed is None:
            return
        self.ed.action_aug_spawn_now()
        self._after_aug_action()

    def _aug_delay(self) -> None:
        if self.ed is None:
            return
        self.ed.action_aug_delay(7)
        self._after_aug_action()

    def _aug_zero(self) -> None:
        if self.ed is None:
            return
        self.ed.action_aug_zero_presence()
        self._after_aug_action()

    def _aug_end(self) -> None:
        if self.ed is None:
            return
        self.ed.action_aug_end()
        self._after_aug_action()

    def _aug_set_escapes(self) -> None:
        if self.ed is None:
            return
        try:
            count = int(float(self.aug_escape_var.get() or 0))
        except ValueError:
            messagebox.showwarning("提示", "放跑次数要填数字。")
            return
        try:
            self.ed.action_aug_set_escapes(count)
        except savegame.EditError as exc:
            messagebox.showwarning("提示", str(exc))
            return
        self._after_aug_action()

    def _aug_restart(self) -> None:
        if self.ed is None:
            return
        self.ed.action_aug_restart()
        self._after_aug_action()

    # -- 鉴定（appraisal）----------------------------------------------------

    def _build_appraisal_tab(self) -> None:
        assert self.ed is not None
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="鉴定")
        scroller = itemeditor.ScrollFrame(shell)
        scroller.pack(fill="both", expand=True)
        outer = ttk.Frame(scroller.inner, padding=(16, 12))
        outer.pack(fill="both", expand=True)

        titles = ttk.Frame(outer)
        titles.pack(fill="x")
        self.appr_summary = ttk.Label(titles, text="", style="Title.TLabel")
        self.appr_summary.pack(anchor="w")
        self.appr_stats = ttk.Label(titles, text="", style="H1.TLabel")
        self.appr_stats.pack(anchor="w", pady=(2, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 4))
        ttk.Button(buttons, text="明天必来鉴定客", style="Accent.TButton",
                   command=self._appr_force).pack(side="left")
        ttk.Button(buttons, text="等级 +1", command=self._appr_level_up).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="进度填满", command=self._appr_fill).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="迟到天数清零", command=self._appr_reset).pack(side="left", padx=(8, 0))

        rules = ttk.LabelFrame(outer, text=" 游戏里的规则（从代码里读出来的） ", padding=(12, 8))
        rules.pack(fill="x", pady=(10, 0))
        ttk.Label(
            rules,
            text=(
                "· 只有把「鉴定委员会证书」放进展示柜，鉴定客才会被抽到。\n"
                "· 概率 = 等级基础值 × (2 + 迟到天数) / 2，封顶 100%（AppraisalRankHelper.GetAttractionChance）。\n"
                "· 等级基础值：cert1 33% / cert2 42% / cert3 48% / cert4 55%。\n"
                "· 迟到天数：当天没来 +1，来了清零；所以最多几天就必来。"
            ),
            justify="left",
            wraplength=880,
        ).pack(anchor="w")

        grid = ttk.LabelFrame(outer, text=" 状态与数值 ", padding=(12, 10))
        grid.pack(fill="x", pady=(10, 0))

        values = ttk.Frame(grid)
        values.pack(fill="x")
        for label, path, width in (
            ("等级", savegame.P + ("appraiserLevel",), 6),
            ("进度", savegame.P + ("appraisalProgress",), 8),
            ("迟到天数", savegame.P + ("storeClientManager", "appraisalDaysMissed"), 8),
        ):
            cell = ttk.Frame(values)
            cell.pack(side="left", padx=(0, 22))
            ttk.Label(cell, text=label, width=10).pack(side="left")
            self._crime_entry(cell, Field(label, path, INT, 0, 9999), width=width)

        self.appr_certs = ttk.Label(grid, text="", style="Sub.TLabel")
        self.appr_certs.pack(anchor="w", pady=(8, 0))

        self.appr_note = ttk.Label(outer, text="", style="Sub.TLabel", wraplength=740, justify="left")
        self.appr_note.pack(anchor="w", pady=(10, 0))

        self._refresh_appraisal()

    def _refresh_appraisal(self) -> None:
        if self.ed is None or not hasattr(self, "appr_summary"):
            return
        state = self.ed.read_appraisal()
        level = state["level"]
        names = ("初级", "中级", "高级", "特级")
        self.appr_summary.configure(
            text=f"鉴定 · 等级 {level}（{names[level] if level < len(names) else level}）"
            f" · 每天 {state['chance']}% 来客"
        )
        self.appr_stats.configure(
            text=(
                f"进度 {state['progress']:g}/{state['threshold']} · 迟到 {state['days_missed']} 天"
                f" · 展示柜证书 {('、'.join(state['certs']) or '无')}"
            )
        )
        self.appr_certs.configure(
            text=(
                f"当前等级需要 {state['cert_id']} 在展示柜里："
                + ("已就位 ✓" if state["cert_shown"] else "缺（不放证书就永远不来鉴定客）")
            )
        )
        self.appr_note.configure(
            text=(
                f"等级基础概率 {state['base_chance']}%，迟到 {state['days_missed']} 天 → "
                f"今天实际 {state['chance']}%（公式：基础 × (2+迟到) / 2，封顶 100）。"
                "点「明天必来鉴定客」会把迟到天数填到刚好 100%。"
            )
        )

    def _after_appr_action(self) -> None:
        self._sync_appraisal_fields()
        self._refresh_appraisal()
        self._update_pending()

    def _sync_appraisal_fields(self) -> None:
        if self.ed is None:
            return
        state = self.ed.read_appraisal()
        mapping = {
            savegame.P + ("appraiserLevel",): state["level"],
            savegame.P + ("appraisalProgress",): state["progress"],
            savegame.P + ("storeClientManager", "appraisalDaysMissed"): state["days_missed"],
        }
        for path, value in mapping.items():
            entry = self._fields.get(path)
            if not entry:
                continue
            spec, var, _initial = entry
            var.set(fmt_number(value))
            original = self.ed.original_value(path)
            self._fields[path] = (spec, var, "" if original is None else str(original))

    def _appr_force(self) -> None:
        if self.ed is None:
            return
        self.ed.action_appraisal_force_today()
        self._after_appr_action()

    def _appr_level_up(self) -> None:
        if self.ed is None:
            return
        state = self.ed.read_appraisal()
        target = state["level"] + 1
        if target >= len(savegame.SlotEditor.APPRAISAL_RANKS):
            messagebox.showinfo("提示", "已经是最高等级了。")
            return
        need = savegame.SlotEditor.APPRAISAL_RANKS[target][0]
        if need not in state["certs"]:
            if not messagebox.askyesno(
                "升级会断客",
                f"升到 {target} 级后必须把 {need} 放进展示柜，否则鉴定客完全不会来。\n\n"
                f"你现在柜里只有：{'、'.join(state['certs']) or '无'}。\n\n仍然升级？",
            ):
                return
        self.ed.action_appraisal_set_level(target)
        self._after_appr_action()

    def _appr_fill(self) -> None:
        if self.ed is None:
            return
        self.ed.action_appraisal_fill_progress()
        self._after_appr_action()

    def _appr_reset(self) -> None:
        if self.ed is None:
            return
        self.ed.action_appraisal_reset_missed()
        self._after_appr_action()

    # -- factions ------------------------------------------------------------

    def _build_faction_tab(self) -> None:
        assert self.ed is not None
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text="声望")

        ttk.Label(
            outer,
            text="声望值上限大约是 100，每个门派的特权在阈值处解锁。改完顺手点「全部拉满」最省事。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Button(head, text="全部拉满（含特权解锁）", command=self._quick_max_factions).pack(side="left")
        self.faction_summary = ttk.Label(head, text="", style="Sub.TLabel")
        self.faction_summary.pack(side="left", padx=(12, 0))

        self.faction_rows = []
        grid = ttk.Frame(outer)
        grid.pack(fill="both", expand=True, pady=(12, 0))
        grid.columnconfigure(1, weight=1)

        reps = self.ed.node(savegame.P + ("storeReputations",))
        for i, rep in enumerate(reps.items):
            faction_display = rep["factionDisplay"].value
            faction_id = rep["factionId"].value
            amount_node = rep["amount"]
            perks = rep.get("perks", default=None)
            perk_count = len(perks) if perks is not None else 0
            unlocked = 0
            if perks is not None:
                for perk in perks.items:
                    if perk.get("isUnlocked", default=None) is not None and perk["isUnlocked"].value:
                        unlocked += 1

            ttk.Label(grid, text=faction_display, font=("Microsoft YaHei UI", 10, "bold")).grid(
                row=i, column=0, sticky="w", pady=4
            )

            path = savegame.P + ("storeReputations", i, "amount")
            initial = fmt_number(amount_node.value)
            var = tk.StringVar(value=initial)
            entry = ttk.Entry(grid, textvariable=var, width=10, justify="right")
            entry.grid(row=i, column=1, sticky="w", padx=(12, 8))
            var.trace_add("write", lambda *_a: self._update_pending())

            ttk.Label(
                grid, text=f"{faction_id}   ·   特权 {unlocked}/{perk_count}", style="Sub.TLabel"
            ).grid(row=i, column=2, sticky="w")

            ttk.Button(
                grid,
                text="拉满",
                width=6,
                command=lambda idx=i, name=faction_display: self._quick_faction(idx, name),
            ).grid(row=i, column=3, sticky="w", padx=(12, 0))

            self._fields[path] = (Field(f"{faction_display} 声望", path, FLOAT, 0, 1000), var, initial)

        self._update_faction_summary()

    def _update_faction_summary(self) -> None:
        if not hasattr(self, "faction_summary") or self.ed is None:
            return
        reps = self.ed.node(savegame.P + ("storeReputations",))
        values = []
        for i, rep in enumerate(reps.items):
            path = savegame.P + ("storeReputations", i, "amount")
            try:
                values.append(float(self.ed.read(path)))
            except Exception:
                values.append(0.0)
        self.faction_summary.configure(text="当前：" + "  ".join(f"{v:.0f}" for v in values))

    # -- inventory -----------------------------------------------------------

    def _build_inventory_tab(self) -> None:
        assert self.ed is not None
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text="物品")

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="背包").pack(side="left")
        self.inv_combo = ttk.Combobox(top, state="readonly", width=26)
        self.inv_combo.pack(side="left", padx=(8, 12))
        self.inv_combo.bind("<<ComboboxSelected>>", self._on_inventory_change)

        ttk.Label(top, text="估值 ×", style="Sub.TLabel").pack(side="left", padx=(8, 2))
        self.inv_factor_var = tk.StringVar(value="2")
        ttk.Entry(top, textvariable=self.inv_factor_var, width=6, justify="right").pack(side="left")
        ttk.Button(top, text="放大全部物品估值", command=self._quick_multiply_values).pack(
            side="left", padx=(6, 0)
        )

        self.inv_hint_var = tk.StringVar(
            value="这台游戏里每件物品是独立的格子物件，「估值」才影响卖给顾客的价格。"
        )
        ttk.Label(outer, textvariable=self.inv_hint_var, style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

        columns = ("name", "contents", "value", "types", "identifier")
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True, pady=(8, 8))
        self.inv_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse")
        for col, text, width, anchor in (
            ("name", "名称", 180, "w"),
            ("contents", "内容物", 165, "w"),
            ("value", "估值", 65, "e"),
            ("types", "类型", 125, "w"),
            ("identifier", "标识", 150, "w"),
        ):
            self.inv_tree.heading(col, text=text)
            self.inv_tree.column(col, width=width, anchor=anchor)
        self.inv_tree.pack(side="left", fill="both", expand=True)
        inv_scroll = ttk.Scrollbar(holder, orient="vertical", command=self.inv_tree.yview)
        inv_scroll.pack(side="right", fill="y")
        self.inv_tree.configure(yscrollcommand=inv_scroll.set)
        self.inv_tree.bind("<<TreeviewSelect>>", self._on_item_select)

        edit = ttk.Frame(outer)
        edit.pack(fill="x")
        ttk.Label(edit, text="选中物品的估值 →").pack(side="left")
        self.item_value_var = tk.StringVar(value="")
        ttk.Entry(edit, textvariable=self.item_value_var, width=12, justify="right").pack(
            side="left", padx=(6, 6)
        )
        ttk.Button(edit, text="应用这件", command=self._apply_item_value).pack(side="left")
        ttk.Button(edit, text="编辑全部属性…", command=self._edit_item_properties).pack(side="left", padx=(8, 0))
        ttk.Button(edit, text="自定义模组…", command=self._open_module_workshop).pack(side="left", padx=(8, 0))
        ttk.Button(edit, text="种子 DNA…", command=self._open_seed_dna).pack(side="left", padx=(8, 0))
        ttk.Button(edit, text="修复物品特性", command=self._repair_item_features).pack(side="left", padx=(8, 0))
        ttk.Button(edit, text="删除选中物品", command=self._delete_selected_item).pack(side="left", padx=(8, 0))


        inventories = self.ed.inventories()
        if not inventories:
            self.inv_combo["values"] = ["（这个存档里没有背包数据）"]
            self.inv_combo.current(0)
            return
        self._inv_labels = {f"{label}（{count} 件）": key for key, label, count in inventories}
        self.inv_combo["values"] = list(self._inv_labels)
        self.inv_combo.current(0)
        self._inv_key = next(iter(self._inv_labels.values()))
        self._refresh_items()

    def _on_inventory_change(self, _event=None) -> None:
        key = getattr(self, "_inv_labels", {}).get(self.inv_combo.get())
        if key:
            self._inv_key = key
            self._refresh_items()

    def _refresh_items(self) -> None:
        if not self._inv_key or self.ed is None:
            return
        self.inv_tree.delete(*self.inv_tree.get_children())
        items = self.ed.inventory_items(self._inv_key)
        for item in items:
            value_node = item["unit_value"]
            value = value_node.value if value_node is not None else ""
            self.inv_tree.insert(
                "",
                "end",
                iid=str(item["index"]),
                values=(
                    item["name"],
                    itemprops.container_summary(item["node"])
                    or seed_dna.summary(item["node"])
                    or animal_dna.summary(item["node"]),
                    value,
                    ",".join(item["types"]),
                    item["identifier"],
                ),
            )
        self.inv_hint_var.set(
            f"这台游戏里每件物品是独立的格子物件，「估值」才影响卖给顾客的价格。　当前 {len(items)} 件。"
        )
        self.item_value_var.set("")

    def _on_item_select(self, _event=None) -> None:
        sel = self.inv_tree.selection()
        if not sel or self.ed is None or not self._inv_key:
            return
        index = int(sel[0])
        item = self.ed.inventory_items(self._inv_key)[index]
        node = item["unit_value"]
        self.item_value_var.set(fmt_number(node.value) if node is not None else "")

    def _apply_item_value(self) -> None:
        if self.ed is None or not self._inv_key:
            return
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一件物品。")
            return
        try:
            value = float(self.item_value_var.get().replace(",", ""))
        except ValueError:
            messagebox.showwarning("数值不对", "估值要填数字。")
            return
        index = int(sel[0])
        self.ed.stage_inventory_item(self._inv_key, index, "unitValue", value)
        item = self.ed.inventory_items(self._inv_key)[index]
        self.inv_tree.set(sel[0], "value", fmt_number(value))
        self.set_status(f"已暂存：{item['name']} 估值 → {fmt_number(value)}")
        self._update_pending()

    def _multiply_with(self, factor: float) -> None:
        self.inv_factor_var.set(fmt_number(factor))
        self._quick_multiply_values()

    def _quick_multiply_values(self) -> None:
        if self.ed is None:
            return
        try:
            factor = float(self.inv_factor_var.get())
        except ValueError:
            messagebox.showwarning("数值不对", "倍数要填数字。")
            return
        if factor == 1:
            return
        self.ed.action_multiply_item_values(factor)
        self._refresh_items()
        self.set_status(f"已暂存：所有背包里的物品估值 ×{fmt_number(factor)}")
        self._update_pending()

    def _edit_item_properties(self) -> None:
        if self.ed is None or not self._inv_key:
            return
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一件要编辑的物品。")
            return
        existing = getattr(self, "_item_editor", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        try:
            dialog = itemeditor.ItemEditor(
                self.root, self.ed, self._inv_key, int(sel[0]), on_saved=self._after_item_edit
            )
        except Exception as exc:
            messagebox.showerror("打不开属性编辑器", str(exc))
            return
        self._item_editor = dialog

    def _repair_item_features(self) -> None:
        if self.ed is None:
            messagebox.showinfo("提示", "先选一个存档槽位。")
            return
        try:
            added = self.ed.repair_item_features()
        except Exception as exc:
            messagebox.showerror("修复失败", str(exc))
            return
        if not added:
            messagebox.showinfo(
                "物品特性",
                "所有带耐久 / 金属纯度的物品都已有特性记录，不需要修。",
            )
            return
        self._update_pending()
        messagebox.showinfo(
            "物品特性",
            f"补了 {added} 条特性记录。\n\n"
            "这些记录让读档后物品重新挂上耐久 / 纯度特性，tooltip 才不会崩。\n"
            "记得点「保存到游戏」。",
        )

    def _open_seed_dna(self) -> None:
        if self.ed is None or not self._inv_key:
            messagebox.showinfo("提示", "先选一个存档和背包。")
            return
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一颗种子。")
            return
        index = int(sel[0])
        node = self.ed.inventory_items(self._inv_key)[index]["node"]
        if not seed_dna.is_seed(node):
            messagebox.showinfo("不是种子", "选中的物品没有种子基因（没有 SEED_ITEM_TAG／PLANT_TRAIT 标签）。")
            return
        try:
            itemeditor.ItemEditor(
                self.root, self.ed, self._inv_key, index,
                on_saved=self._after_item_edit, initial_tab="种子 DNA",
            )
        except Exception as exc:
            messagebox.showerror("打不开种子编辑器", str(exc))

    def _open_module_workshop(self) -> None:
        if self.ed is None:
            messagebox.showinfo("提示", "先选一个存档槽位。")
            return
        existing = getattr(self, "_module_editor", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        try:
            dialog = module_editor.ModuleWorkshop(self.root, self.ed, self.catalog, on_created=self._after_item_edit)
        except Exception as exc:
            messagebox.showerror("打不开模组工坊", str(exc))

    def _after_item_edit(self) -> None:
        self._refresh_items()
        self._update_pending()
        self.set_status("已暂存物品属性修改，记得点「保存到游戏」")

    def _delete_selected_item(self) -> None:
        if self.ed is None or not self._inv_key:
            return
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一件要删的物品。")
            return
        index = int(sel[0])
        if index == 0:
            messagebox.showwarning("不能删", "第一件是背包本身，删了背包就没了。\n想删空整个背包请直接清空里面的物品。")
            return
        item = self.ed.inventory_items(self._inv_key)[index]
        if not messagebox.askyesno(
            "确认删除",
            f"从「{self.inv_choice_label()}」里删掉：\n\n{item['name']}（{item['identifier']}）\n\n"
            "删除会立即重排背包里的编号，保存后生效。",
        ):
            return
        try:
            name = self.ed.inventory_remove(self._inv_key, index)
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc))
            return
        self._refresh_items()
        self._update_pending()
        self.set_status(f"已暂存：删除 {name}")

    def inv_choice_label(self) -> str:
        try:
            return self.inv_combo.get().split("（")[0] or "背包"
        except Exception:
            return "背包"

    # -- item catalogue ------------------------------------------------------

    def _refresh_catalog(self) -> None:
        """Re-scan the profile so newly seen items land in the encyclopedia.

        Harvesting means parsing every save (megabytes), so it is skipped when
        no save file has changed since the last pass -- otherwise every
        "重新扫描" would freeze the window for a second or two.
        """
        if self.profile is None:
            return
        stamp = self._profile_stamp()
        added = 0
        if stamp != getattr(self, "_catalog_stamp", None):
            try:
                added = self.catalog.harvest_profile(self.profile)
            except Exception:
                added = 0
            self._catalog_stamp = stamp
        if hasattr(self, "catalog_tree"):
            self._fill_catalog_tree()
        if added:
            self.set_status(f"物品百科新增 {added} 件（共 {len(self.catalog)} 件）")

    def _profile_stamp(self):
        if self.profile is None:
            return None
        try:
            return tuple(sorted(
                (p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in self.profile.folder.glob("save_*.es3")
            ))
        except OSError:
            return None

    def _build_catalog_tab(self) -> None:
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text="百科")

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="搜索").pack(side="left")
        self.cat_search = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.cat_search, width=16)
        search.pack(side="left", padx=(6, 12))
        self.cat_search.trace_add("write", lambda *_a: self._fill_catalog_tree())

        ttk.Label(top, text="类型").pack(side="left")
        self.cat_category = ttk.Combobox(top, state="readonly", width=14)
        self.cat_category.pack(side="left", padx=(6, 12))
        self.cat_category.bind("<<ComboboxSelected>>", lambda _e: self._fill_catalog_tree())

        row2 = ttk.Frame(outer)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="放入").pack(side="left")
        self.cat_target = ttk.Combobox(row2, state="readonly", width=20)
        self.cat_target.pack(side="left", padx=(6, 12))
        ttk.Button(row2, text="添加选中物品", style="Accent.TButton", command=self._add_catalog_item).pack(
            side="left"
        )
        self.cat_default_build = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="按游戏默认生成", variable=self.cat_default_build).pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(
            row2, text="（去脏物/买价/生长状态）", style="Sub.TLabel"
        ).pack(side="left", padx=(8, 0))

        self.cat_count = ttk.Label(outer, text="", style="Sub.TLabel")
        self.cat_count.pack(anchor="w", pady=(8, 2))
        ttk.Label(
            outer,
            text=(
                "收录的是从你的存档里自动学到的全部物品（含游戏自己的历史快照），"
                "每件都用真实的物品数据当模板；添加时会先去掉赃物标记、把买价和耐久恢复成正常值。"
                "双击一行也能直接添加。"
            ),
            style="Sub.TLabel",
            wraplength=840,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        columns = ("name", "category", "value", "size", "identifier")
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True)
        self.catalog_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse")
        for col, text, width, anchor in (
            ("name", "名称", 230, "w"),
            ("category", "类型", 200, "w"),
            ("value", "估值", 80, "e"),
            ("size", "尺寸", 65, "e"),
            ("identifier", "标识", 190, "w"),
        ):
            self.catalog_tree.heading(col, text=text)
            self.catalog_tree.column(col, width=width, anchor=anchor)
        self.catalog_tree.pack(side="left", fill="both", expand=True)
        cat_scroll = ttk.Scrollbar(holder, orient="vertical", command=self.catalog_tree.yview)
        cat_scroll.pack(side="right", fill="y")
        self.catalog_tree.configure(yscrollcommand=cat_scroll.set)
        self.catalog_tree.bind("<<TreeviewSelect>>", self._on_catalog_select)
        self.catalog_tree.bind("<Double-1>", lambda _e: self._add_catalog_item())

        self.cat_detail = tk.StringVar(value="选中一件物品看详细信息。")
        ttk.Label(outer, textvariable=self.cat_detail, style="Sub.TLabel", wraplength=860, justify="left").pack(
            anchor="w", pady=(8, 0)
        )

        self.cat_category["values"] = ["全部类型"] + self.catalog.categories()
        self.cat_category.current(0)
        self._fill_catalog_tree()
        self._fill_catalog_targets()

    def _fill_catalog_tree(self) -> None:
        if not hasattr(self, "catalog_tree"):
            return
        query = self.cat_search.get().strip().lower()
        category = self.cat_category.get() if hasattr(self, "cat_category") else "全部类型"
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        self._catalog_rows = []
        for entry in self.catalog.all():
            if category and category not in ("", "全部类型") and entry.category != category:
                continue
            haystack = f"{entry.name} {entry.identifier} {' '.join(entry.types)}".lower()
            if query and query not in haystack:
                continue
            iid = entry.identifier
            self.catalog_tree.insert(
                "", "end", iid=iid,
                values=(entry.name, entry.category, entry.value, f"{entry.width}×{entry.height}", entry.identifier),
            )
            self._catalog_rows.append(iid)
        if hasattr(self, "cat_count"):
            self.cat_count.configure(
                text=f"共收录 {len(self.catalog)} 件物品 · 当前筛选出 {len(self._catalog_rows)} 件"
            )

    def _fill_catalog_targets(self) -> None:
        if not hasattr(self, "cat_target"):
            return
        if self.ed is None:
            self.cat_target["values"] = ["（先选一个存档槽位）"]
            self.cat_target.current(0)
            return
        self._cat_targets = {label: key for key, label, _count in self.ed.inventories()}
        if not self._cat_targets:
            self.cat_target["values"] = ["（这个存档没有背包）"]
            self.cat_target.current(0)
            return
        self.cat_target["values"] = list(self._cat_targets)
        current = self.inv_choice_label()
        preferred = next((k for k in self._cat_targets if k.startswith(current)), None)
        self.cat_target.set(preferred or list(self._cat_targets)[0])

    def _on_catalog_select(self, _event=None) -> None:
        sel = self.catalog_tree.selection()
        if not sel:
            return
        entry = self.catalog.get(sel[0])
        if entry is None:
            return
        bits = [
            f"{entry.name}（{entry.identifier}）",
            f"类型：{'、'.join(entry.types) if entry.types else '无'}",
            f"估值：{entry.value}",
            f"尺寸：{entry.width}×{entry.height}",
        ]
        spec = self._factory.get(entry.identifier) if hasattr(self, "_factory") else None
        if spec:
            bits.append(f"工厂：{spec['factory']}")
            if spec.get("value") is not None:
                bits.append(f"默认估值：{spec['value']}")
            if spec.get("durability") is not None:
                bits.append(f"默认耐久：{spec['durability']}")
        text = "　|　".join(bits)
        if spec and spec.get("tags"):
            text += "\n默认标签：" + "、".join(spec["tags"])
        if entry.description:
            text += "\n" + entry.description.strip()
        if entry.flavor:
            text += "\n" + entry.flavor.strip()
        self.cat_detail.set(text)

    def _add_catalog_item(self) -> None:
        if self.ed is None:
            messagebox.showinfo("提示", "先选一个存档槽位，再往背包里加东西。")
            return
        sel = self.catalog_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在百科列表里选一件物品。")
            return
        key = getattr(self, "_cat_targets", {}).get(self.cat_target.get())
        if not key:
            messagebox.showwarning("提示", "没有选择目标背包。")
            return
        entry = self.catalog.get(sel[0])
        if entry is None:
            return
        template = entry.template
        if getattr(self, "cat_default_build", None) is not None and self.cat_default_build.get():
            try:
                template = itemcatalog.defaultize_template(template, self._factory.get(entry.identifier))
            except Exception:
                template = entry.template
        try:
            name = self.ed.inventory_add(key, template)
        except Exception as exc:
            messagebox.showerror("添加失败", str(exc))
            return
        if self._inv_key == key:
            self._refresh_items()
        self._fill_catalog_targets()
        self._update_pending()
        self.set_status(f"已暂存：把 {name} 放进 {self.cat_target.get()}")

    # -- quick-add presets ---------------------------------------------------

    def _build_presets_tab(self) -> None:
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text="模板")

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="来源背包").pack(side="left")
        self.preset_src = ttk.Combobox(top, state="readonly", width=22)
        self.preset_src.pack(side="left", padx=(6, 8))
        if self.ed is not None:
            self._preset_src_map = {label: key for key, label, _c in self.ed.inventories()}
            self.preset_src["values"] = list(self._preset_src_map)
            if self._preset_src_map:
                self.preset_src.current(0)
        ttk.Button(top, text="存为模板", command=self._preset_capture).pack(side="left")
        ttk.Button(top, text="删除选中模板", command=self._preset_delete).pack(side="left", padx=(8, 0))

        columns = ("name", "kinds", "total", "containers", "source", "created")
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True, pady=(10, 8))
        self.preset_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse")
        for col, text, width, anchor_ in (
            ("name", "模板", 175, "w"),
            ("kinds", "顶层", 55, "e"),
            ("total", "含嵌套", 65, "e"),
            ("containers", "箱子", 55, "e"),
            ("source", "来源", 100, "w"),
            ("created", "创建时间", 140, "w"),
        ):
            self.preset_tree.heading(col, text=text)
            self.preset_tree.column(col, width=width, anchor=anchor_)
        self.preset_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.preset_tree.yview)
        scroll.pack(side="right", fill="y")
        self.preset_tree.configure(yscrollcommand=scroll.set)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x")
        ttk.Label(bottom, text="放入").pack(side="left")
        self.preset_dst = ttk.Combobox(bottom, state="readonly", width=16)
        self.preset_dst.pack(side="left", padx=(6, 12))
        if self.ed is not None:
            self._preset_dst_map = {label: key for key, label, _c in self.ed.inventories()}
            self.preset_dst["values"] = list(self._preset_dst_map)
            if self._preset_dst_map:
                self.preset_dst.current(0)
        self.preset_copies = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="按原数量", variable=self.preset_copies).pack(side="left", padx=(0, 8))
        self.preset_skip_existing = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bottom, text="跳过已有", variable=self.preset_skip_existing
        ).pack(side="left", padx=(0, 8))
        self.preset_spread = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bottom, text="摊到其他背包", variable=self.preset_spread
        ).pack(side="left", padx=(0, 8))
        ttk.Button(bottom, text="添加到背包", style="Accent.TButton", command=self._preset_apply).pack(
            side="right"
        )
        self.preset_status = ttk.Label(outer, text="", style="Sub.TLabel")
        self.preset_status.pack(anchor="w", pady=(8, 0))

        self._refresh_presets()

    def _refresh_presets(self) -> None:
        if not hasattr(self, "preset_tree"):
            return
        self.preset_tree.delete(*self.preset_tree.get_children())
        for preset in self.presets.presets:
            self.preset_tree.insert(
                "", "end", iid=preset.id,
                values=(preset.name, preset.kinds, preset.total, preset.containers,
                        preset.source, preset.created),
            )
        if self.presets.presets:
            first = self.preset_tree.get_children()[0]
            self.preset_tree.selection_set(first)
        self.preset_status.configure(
            text=f"共 {len(self.presets.presets)} 个模板。模板存在工具目录的 presets.json，可套用到任意存档。"
        )

    def _preset_capture(self) -> None:
        if self.ed is None:
            messagebox.showinfo("提示", "先选一个存档槽位。")
            return
        key = getattr(self, "_preset_src_map", {}).get(self.preset_src.get())
        if not key:
            messagebox.showwarning("提示", "先选来源背包。")
            return
        default = f"第{self.ed.value(savegame.S + ('dayCounter',))}天 {self.preset_src.get()}"
        name = simpledialog.askstring("存为模板", "模板名字：", initialvalue=default, parent=self.root)
        if not name:
            return
        try:
            preset = self.presets.capture(self.ed, key, name.strip())
        except Exception as exc:
            messagebox.showerror("创建模板失败", str(exc))
            return
        self._refresh_presets()
        self.preset_tree.selection_set(preset.id)
        self.preset_status.configure(text=f"已保存模板「{preset.name}」：{preset.kinds} 种 / {preset.total} 件")

    def _preset_apply(self) -> None:
        if self.ed is None:
            messagebox.showinfo("提示", "先选一个存档槽位。")
            return
        sel = self.preset_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个模板。")
            return
        preset = self.presets.get(sel[0])
        key = getattr(self, "_preset_dst_map", {}).get(self.preset_dst.get())
        if preset is None or not key:
            messagebox.showwarning("提示", "没有选中模板或目标背包。")
            return
        existing = set()
        if self.preset_skip_existing.get():
            existing = {it["identifier"] for it in self.ed.inventory_items(key)}
        wanted = [r for r in preset.roots if r.identifier not in existing]
        duplicates = len(preset.roots) - len(wanted)

        if not wanted:
            self.preset_status.configure(text="这个背包里已经有全部物品了。")
            return

        bags = [key]
        if self.preset_spread.get():
            for bag_key, _label, _count in self.ed.inventories():
                if bag_key not in bags and bag_key != "soldInvJSON":
                    bags.append(bag_key)

        total_items = 0
        total_containers = 0
        added_roots = 0
        remaining = wanted
        used = []
        try:
            for bag_key in bags:
                if not remaining:
                    break
                roots_ok, roots_skipped, items, containers = self.ed.inventory_add_forest(
                    bag_key, remaining
                )
                if roots_ok:
                    added_roots += roots_ok
                    total_items += items
                    total_containers += containers
                    used.append(bag_key)
                remaining = roots_skipped
        except Exception as exc:
            messagebox.showerror("添加失败", str(exc))
            return
        skipped = remaining
        self._refresh_items()
        self._update_pending()
        parts = [f"已加入 {added_roots} 个顶层（共 {total_items} 件物品）"]
        if total_containers:
            parts.append(f"其中 {total_containers} 个箱子带着里面的东西")
        if duplicates:
            parts.append(f"{duplicates} 个已有跳过")
        if skipped:
            parts.append(f"{len(skipped)} 个没放下")
        if len(used) > 1:
            parts.append(f"分布在 {len(used)} 个背包")
        text = "，".join(parts)
        self.set_status(text + "，记得点「保存到游戏」")
        self.preset_status.configure(text=text)
        if skipped:
            messagebox.showwarning(
                "部分物品没放下",
                f"{len(skipped)} 个顶层物品因为背包格子不够被跳过（它们里面的东西也没放）。\n\n"
                "可以清空背包、把大件挪到别的背包，或者先把「跳过已有」关掉再试。",
            )

    def _preset_delete(self) -> None:
        sel = self.preset_tree.selection()
        if not sel:
            return
        preset = self.presets.get(sel[0])
        if preset is None:
            return
        if not messagebox.askyesno("删除模板", f"删除模板「{preset.name}」？"):
            return
        self.presets.remove(preset.id)
        self._refresh_presets()

    # -- unlocks -------------------------------------------------------------

    def _build_unlock_tab(self) -> None:
        assert self.ed is not None
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text="解锁")

        ttk.Label(
            outer,
            text="这些都是「一键暂存」，不会立刻写盘 —— 看完列表再点右下角保存。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")

        buttons = [
            ("全部网络升级 → 已购买", lambda: self._quick(lambda e: e.action_buy_all_upgrades(2))),
            ("全部网络升级 → 可购买", lambda: self._quick(lambda e: e.action_buy_all_upgrades(1))),
            ("全部服务解锁", lambda: self._quick(lambda e: e.action_unlock_services())),
            ("解锁远征 / 全部地点", lambda: self._quick(lambda e: e.action_unlock_features())),
            ("关闭全部教程", lambda: self._quick(lambda e: e.action_skip_tutorials())),
            ("清除犯罪记录", lambda: self._quick(lambda e: e.action_clear_crime())),
            ("治好伤势 / 戒断清零", lambda: self._quick(lambda e: e.action_clear_wounds())),
            ("租金倒计时 → 120 天", lambda: self._quick(lambda e: e.action_delay_rent(120))),
            ("补给站补满库存", lambda: self._quick(lambda e: e.action_restock_commissary())),
        ]
        for i, (text, cmd) in enumerate(buttons):
            btn = ttk.Button(actions, text=text, command=cmd, width=22)
            btn.grid(row=i // 3, column=i % 3, padx=(0, 8), pady=6, sticky="w")

        mid = ttk.LabelFrame(outer, text=" 网络升级状态 ", padding=(12, 8))
        mid.pack(fill="both", expand=True, pady=(14, 0))

        columns = ("id", "state", "cost")
        self.up_tree = ttk.Treeview(mid, columns=columns, show="headings", height=12)
        for col, text, width, anchor in (
            ("id", "升级", 320, "w"),
            ("state", "状态", 140, "w"),
            ("cost", "价格", 100, "e"),
        ):
            self.up_tree.heading(col, text=text)
            self.up_tree.column(col, width=width, anchor=anchor)
        self.up_tree.pack(fill="both", expand=True, side="left")
        up_scroll = ttk.Scrollbar(mid, orient="vertical", command=self.up_tree.yview)
        up_scroll.pack(side="right", fill="y")
        self.up_tree.configure(yscrollcommand=up_scroll.set)
        self._refresh_upgrades()

    def _refresh_upgrades(self) -> None:
        if self.ed is None:
            return
        self.up_tree.delete(*self.up_tree.get_children())
        upgrades = self.ed.node(savegame.P + ("networkUpgrade",))
        states = {0: "未解锁", 1: "可购买", 2: "已购买"}
        for key in upgrades.keys_list():
            entry = upgrades[key]
            state_node = entry.get("state", default=None)
            cost_node = entry.get("cost", default=None)
            if state_node is None:
                continue
            current = int(self.ed.read(savegame.P + ("networkUpgrade", key, "state")))
            staged = self.ed.read(savegame.P + ("networkUpgrade", key, "state"))
            label = states.get(int(staged), str(staged))
            if int(staged) != state_node.value:
                label += " ✱"
            self.up_tree.insert(
                "",
                "end",
                iid=key,
                values=(key, label, cost_node.value if cost_node is not None else ""),
            )

    # -- backups -------------------------------------------------------------

    def _build_backup_tab(self) -> None:
        assert self.ed is not None and self.profile is not None
        outer = ttk.Frame(self.nb, padding=(16, 12))
        self.nb.add(outer, text="备份")

        ttk.Label(
            outer,
            text=(
                "每次保存都会先把这个槽位复制到 _editor_backups 文件夹（游戏不会扫描到这里）。\n"
                "想反悔就选一份备份点「还原」——还原前也会再备份一次当前文件。"
            ),
            style="Sub.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="立即备份当前槽位", command=self._backup_now).pack(side="left")
        ttk.Button(bar, text="还原选中的备份", command=self._restore_backup).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="清理旧备份（保留 40 份）", command=self._prune_backups).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(bar, text="导出当前槽位为 JSON", command=self._export_json).pack(side="left", padx=(8, 0))

        columns = ("file", "size", "time")
        self.backup_tree = ttk.Treeview(outer, columns=columns, show="headings", height=14)
        for col, text, width, anchor in (
            ("file", "备份文件", 340, "w"),
            ("size", "大小", 110, "e"),
            ("time", "时间", 180, "w"),
        ):
            self.backup_tree.heading(col, text=text)
            self.backup_tree.column(col, width=width, anchor=anchor)
        self.backup_tree.pack(fill="both", expand=True)
        self._refresh_backups()

    def _refresh_backups(self) -> None:
        if self.profile is None or not hasattr(self, "backup_tree"):
            return
        self.backup_tree.delete(*self.backup_tree.get_children())
        import time as _time

        for path in self.profile.list_backups():
            stat = path.stat()
            self.backup_tree.insert(
                "",
                "end",
                iid=str(path),
                values=(
                    path.name,
                    f"{stat.st_size / 1024:.0f} KB",
                    _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(stat.st_mtime)),
                ),
            )

    def _backup_now(self) -> None:
        if self.profile is None or self.slot_id is None:
            return
        dest = self.profile.backup(self.profile.slot_path(self.slot_id))
        if dest:
            self._refresh_backups()
            self.set_status(f"已备份到 {dest.name}")
        else:
            messagebox.showwarning("提示", "当前槽位没有存档文件。")

    def _restore_backup(self) -> None:
        sel = self.backup_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一份备份。")
            return
        source = Path(sel[0])
        target_name = source.name.split(".")[0] + ".es3"
        target = self.profile.folder / target_name if self.profile else None
        if target is None or not target.exists():
            messagebox.showwarning("提示", "这个备份对不上任何当前存档文件。")
            return
        if not messagebox.askyesno(
            "确认还原",
            f"用这份备份覆盖 {target.name}？\n\n{source.name}\n\n当前文件会先被再备份一次。",
        ):
            return
        self.profile.backup(target)
        target.write_bytes(source.read_bytes())
        self.set_status(f"已从 {source.name} 还原 {target.name}")
        if self.slot_id and target.name == f"save_{self.slot_id}.es3":
            self.load_slot(self.slot_id)

    def _prune_backups(self) -> None:
        if self.profile is None:
            return
        self.profile.prune_backups(40)
        self._refresh_backups()
        self.set_status("已清理旧备份，保留最近 40 份。")

    def _export_json(self) -> None:
        if self.ed is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出为 JSON",
            defaultextension=".json",
            initialfile=f"save_{self.slot_id}.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        import es3json

        Path(path).write_text(
            es3json.dumps(self.ed.doc.root.to_python()), encoding="utf-8"
        )
        self.set_status(f"已导出到 {path}")

    # -- staging helpers -----------------------------------------------------

    def _quick(self, fn) -> None:
        """Run a one-click action against the editor, then refresh widgets."""
        if self.ed is None:
            return
        try:
            fn(self.ed)
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc))
            return
        self._sync_widgets_from_editor()
        self._update_pending()

    def _quick_cash_add(self, amount: int) -> None:
        self._quick(lambda e: e.action_add_cash(amount))

    def _quick_cash_set(self, value: int) -> None:
        self._quick(lambda e: e.action_set_cash(value))

    def _quick_delay_rent(self) -> None:
        self._quick(lambda e: e.action_delay_rent(99))

    def _quick_max_factions(self) -> None:
        def run(editor):
            editor.action_max_factions(100.0)

        self._quick(run)
        self._update_faction_summary()

    def _quick_faction(self, index: int, name: str) -> None:
        if self.ed is None:
            return
        self.ed.stage(
            savegame.P + ("storeReputations", index, "amount"), 100.0, f"{name} 声望 = 100"
        )
        reps = self.ed.node(savegame.P + ("storeReputations",))
        perks = reps[index].get("perks", default=None)
        if perks is not None:
            for j, perk in enumerate(perks.items):
                if perk.get("isUnlocked", default=None) is not None:
                    self.ed.stage(
                        savegame.P + ("storeReputations", index, "perks", j, "isUnlocked"),
                        True,
                        f"{name}·{perk['perkId'].value}",
                    )
        self._sync_widgets_from_editor()
        self._update_pending()

    def _quick_max_station(self) -> None:
        if self.ed is None:
            return
        for key in ("traffic", "happiness", "order"):
            self.ed.stage(savegame.S + (key,), 100, f"车站 {key} = 100")
        self._sync_widgets_from_editor()
        self._update_pending()

    def _quick_max_water(self) -> None:
        if self.ed is None:
            return
        for key in ("todayGutterflowPurity", "todayRustwaterPurity"):
            self.ed.stage(savegame.S + (key,), 10000, f"水质 {key} = 10000")
        self._sync_widgets_from_editor()
        self._update_pending()

    def _quick_max_powers(self) -> None:
        if self.ed is None:
            return
        for key in ("revPower", "secPower", "blackMarketPower", "upperLevelFriendliness"):
            path = savegame.S + ("storeOperationManager", key)
            try:
                self.ed.stage(path, 100, f"势力 {key} = 100")
            except KeyError:
                continue
        self._sync_widgets_from_editor()
        self._update_pending()

    def _sync_widgets_from_editor(self) -> None:
        """Push staged values back into the entry boxes (after one-click actions)."""
        if self.ed is None:
            return
        # Writing a variable fires its trace -> _update_pending -> _collect, and
        # _collect would see the not-yet-refreshed text as "unchanged" and undo
        # the very one-click action we are rendering. Suppress that round trip.
        self._syncing = True
        try:
            for path, (spec, var, _initial) in self._fields.items():
                try:
                    value = self.ed.read(path)
                except Exception:
                    continue
                if spec.kind == BOOL:
                    var.set(bool(value))
                else:
                    var.set(fmt_number(value))
        finally:
            self._syncing = False
        self._refresh_upgrades()
        self._update_faction_summary()

    # -- collecting + saving -------------------------------------------------

    def _collect(self) -> list[str]:
        """Read every widget, stage what changed. Returns a list of problems."""
        if self.ed is None:
            return []
        problems = []
        for path, (spec, var, initial) in self._fields.items():
            try:
                if spec.kind == BOOL:
                    new_value = bool(var.get())
                    if new_value != bool(self.ed.original_value(path)):
                        self.ed.stage(path, new_value, spec.label)
                    continue

                text = var.get().strip()
                if text == initial:
                    # untouched: never rewrite a value we merely displayed
                    self.ed.unstage(path)
                    continue
                if text == "":
                    problems.append(f"{spec.label}：不能为空")
                    continue
                if spec.kind == STR:
                    if text != initial:
                        self.ed.stage(path, text, spec.label)
                    else:
                        self.ed.unstage(path)
                    continue
                try:
                    number = float(text.replace(",", ""))
                except ValueError:
                    problems.append(f"{spec.label}：{text!r} 不是数字")
                    continue
                if spec.lo is not None and number < spec.lo:
                    problems.append(f"{spec.label}：不能小于 {spec.lo:g}")
                    continue
                if spec.hi is not None and number > spec.hi:
                    problems.append(f"{spec.label}：不能大于 {spec.hi:g}")
                    continue
                if spec.kind == INT:
                    number = int(round(number))
                if not self._values_same(self.ed.original_value(path), number):
                    self.ed.stage(path, number, spec.label)
                else:
                    self.ed.unstage(path)
            except Exception as exc:
                problems.append(f"{spec.label}：{exc}")
        return problems

    @staticmethod
    def _values_same(a, b) -> bool:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
            return abs(float(a) - float(b)) < 1e-9
        return a == b

    def _update_pending(self) -> None:
        if self.ed is None:
            self.pending_var.set("")
            return
        if getattr(self, "_syncing", False):
            return
        try:
            self._collect()  # keep staged state in sync with what is typed
        except Exception:
            pass
        staged = self.ed.count_changes()
        self.pending_var.set(f"待保存 {staged} 处修改" if staged else "")

    def discard(self) -> None:
        if self.ed is None:
            return
        if self.ed.count_changes() and not messagebox.askyesno("放弃修改", "确定丢掉所有暂存的修改吗？"):
            return
        self.ed.clear_staged()
        if self.slot_id is not None:
            self.load_slot(self.slot_id)

    def do_save(self) -> None:
        if self.ed is None:
            return
        self._collect_perks()
        problems = self._collect()
        if problems:
            messagebox.showwarning("还有字段填得不对", "\n".join(problems[:12]))
            return
        changes = self.ed.changes()
        if not changes:
            messagebox.showinfo("没有修改", "所有数值都和存档里一致，不需要保存。")
            return

        if not self._confirm_changes(changes):
            return

        before_counts = {key: len(self.ed.inventory_items(key)) for key, _l, _n in self.ed.inventories()}
        try:
            result = self.ed.save()
        except savegame.EditError as exc:
            messagebox.showerror("保存失败", str(exc))
            self.set_status(f"保存失败：{exc}", warn=True)
            return
        except Exception:
            messagebox.showerror("保存失败", traceback.format_exc(limit=4))
            return

        self._after_save(result, before_counts)
        return

    def _collect_perks(self) -> None:
        if self.ed is None or not hasattr(self, "perk_vars"):
            return
        chosen = self._selected_perk_ids()
        if chosen == list(self._perk_original):
            self.ed.unstage(savegame.P + ("startingPerks",))
            return
        self.ed.stage(
            savegame.P + ("startingPerks",),
            perks.build_entries(chosen, self._perk_catalog),
            f"初始特性（{len(chosen)} 项）",
        )

    def _confirm_changes(self, changes) -> bool:
        top = tk.Toplevel(self.root)
        top.title("确认修改")
        top.transient(self.root)
        top.geometry("640x520")

        ttk.Label(
            top,
            text=f"即将写入 {len(changes)} 处修改，写盘前会自动备份。",
            style="H1.TLabel",
            padding=(14, 12, 14, 2),
        ).pack(anchor="w")
        ttk.Label(
            top,
            text="确认游戏已经完全退出——游戏退出时会把内存里的存档写回去，盖掉这次修改。",
            style="Warn.TLabel",
            padding=(14, 0, 14, 8),
            wraplength=580,
            justify="left",
        ).pack(anchor="w")

        frame = ttk.Frame(top, padding=(14, 0, 14, 8))
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="none", height=14, font=("Consolas", 9))
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)

        for label, old, new in changes[:400]:
            text.insert("end", f"{label}\n    {self._show(old)}  →  {self._show(new)}\n")
        if len(changes) > 400:
            text.insert("end", f"\n… 还有 {len(changes) - 400} 处\n")
        text.configure(state="disabled")

        result = {"ok": False}
        ttk.Separator(top).pack(fill="x")
        bar = ttk.Frame(top, padding=(14, 8, 14, 10))
        bar.pack(fill="x")

        def ok():
            result["ok"] = True
            top.destroy()

        ttk.Button(bar, text="确认写入", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(bar, text="取消", command=top.destroy).pack(side="right", padx=(0, 8))
        itemeditor.present_dialog(top, self.root, 640, 520)
        top.wait_window()
        return result["ok"]

    @staticmethod
    def _show(value) -> str:
        if isinstance(value, float):
            return fmt_number(value)
        text = str(value)
        return text if len(text) <= 60 else text[:57] + "…"

    def _after_save(self, result, before_counts: dict | None = None) -> None:
        backups = result.get("backups") or []
        counts = []
        if before_counts is not None and self.ed is not None:
            for key, label, _n in self.ed.inventories():
                old_n = before_counts.get(key)
                new_n = len(self.ed.inventory_items(key))
                if old_n is not None and old_n != new_n:
                    counts.append(f"{label} {old_n} → {new_n} 件")
        self._refresh_slot_list()
        if self.slot_id is not None:
            self.slot_tree.selection_set(str(self.slot_id))
            self.load_slot(self.slot_id)
        self._refresh_backups()
        summary = f"已写入 {self.slot_id} 号槽位（{len(result.get('changes', []))} 处修改）"
        if counts:
            summary += "，" + "；".join(counts)
        self.set_status(summary + "。备份：" + (backups[0].name if backups else "无"))
        messagebox.showinfo(
            "保存成功",
            summary
            + "\n\n"
            + ("备份文件：\n" + "\n".join(b.name for b in backups) + "\n\n" if backups else "")
            + "现在可以进游戏读档了。",
        )

    # -- status --------------------------------------------------------------

    def _set_actions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (self.save_btn, self.discard_btn):
            widget.configure(state=state)
        if not enabled:
            self.pending_var.set("")

    def set_status(self, text: str, warn: bool = False) -> None:
        self.status_var.set(text)
        self.status_label.configure(style="Warn.TLabel" if warn else "Sub.TLabel")


def enable_dpi_awareness() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main() -> int:
    enable_dpi_awareness()
    root = tk.Tk()
    save_dir = sys.argv[1] if len(sys.argv) > 1 else None
    App(root, save_dir)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
