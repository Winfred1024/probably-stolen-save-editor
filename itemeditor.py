"""Full property editor for one inventory item."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import animal_dna
import itemprops
import savegame
import seed_dna

KIND_HINT = {"str": "文本", "int": "整数", "float": "小数", "bool": "开关"}


def _volume(amount: int) -> str:
    """Microlitres rendered the way a human reads it."""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f} L"
    if amount >= 1000:
        return f"{amount / 1000:.1f} ml"
    return f"{amount} µl"


def fmt_number(value) -> str:
    if isinstance(value, float):
        return str(int(value)) if value == int(value) else f"{value:g}"
    return str(value)


class ScrollFrame(ttk.Frame):
    """A vertically scrollable frame."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.inner = ttk.Frame(self.canvas)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.inner.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def bind_wheel(self, widget):
        widget.bind("<MouseWheel>", self._wheel)


def present_dialog(win: tk.Toplevel, master, width: int, height: int, modal: bool = True) -> None:
    """Put a dialog where the user can actually see it, then grab input.

    The old code grabbed input before the window was even placed, so a dialog
    that popped up behind (or off-screen from) the main window left the whole
    app looking frozen: clicks went to an invisible window that owned the grab.
    """
    try:
        win.update_idletasks()
    except tk.TclError:
        pass

    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    x = y = None
    try:
        px, py = master.winfo_rootx(), master.winfo_rooty()
        pw, ph = master.winfo_width(), master.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 3)
    except (tk.TclError, AttributeError):
        pass
    if x is None:
        x = (screen_w - width) // 2
    if y is None:
        y = (screen_h - height) // 3
    x = max(0, min(x, max(0, screen_w - width)))
    y = max(0, min(y, max(0, screen_h - height)))

    win.geometry(f"{width}x{height}+{x}+{y}")
    try:
        win.deiconify()
        win.lift()
        win.focus_force()
    except tk.TclError:
        pass
    if modal:
        try:
            win.grab_set()
        except tk.TclError:
            # another dialog already holds the grab: stay open but not modal
            pass
    win.bind("<Escape>", lambda _e: win.destroy())


class ItemEditor(tk.Toplevel):
    """Modal editor exposing every stored attribute of one item."""

    def __init__(self, master, editor: savegame.SlotEditor, inv_key: str, index: int,
                 on_saved=None, initial_tab: str | None = None):
        super().__init__(master)
        self.editor = editor
        self.inv_key = inv_key
        self.index = index
        self.on_saved = on_saved

        items = editor.inventory_items(inv_key)
        if not 0 <= index < len(items):
            self.destroy()
            raise ValueError("item index out of range")
        self.item_info = items[index]
        node = self.item_info["node"]

        self.basic = itemprops.read_basic(node)
        self.texts = itemprops.read_texts(node)
        self.shapes = itemprops.read_shapes(node)
        self.types = itemprops.read_types(node)
        self.tags = itemprops.read_tags(node)
        self.container = itemprops.container_info(node)
        self.seed = seed_dna.read_seed(node) if seed_dna.is_seed(node) else None
        self.animal = animal_dna.read_animal(node) if animal_dna.is_animal(node) else None

        self.basic_vars: dict[str, tk.StringVar] = {}
        self.bool_vars: dict[str, tk.BooleanVar] = {}
        self.text_widgets: dict[str, tk.Text] = {}
        self.shape_vars: dict[str, dict] = {}
        self.types_var = tk.StringVar(value=", ".join(self.types))
        self.tag_edits: dict[int, dict] = {}

        self.title(f"编辑物品属性 — {self.item_info['name']}")
        self.transient(master)
        self.minsize(900, 620)
        self._build()
        present_dialog(self, master, 1040, 760)
        if initial_tab:
            for tab_id in self.nb.tabs():
                if self.nb.tab(tab_id, "text") == initial_tab:
                    self.nb.select(tab_id)
                    break

    # -- layout --------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self, padding=(14, 10, 14, 6))
        head.pack(fill="x")
        ttk.Label(head, text=self.item_info["name"], style="H1.TLabel").pack(side="left")
        ttk.Label(
            head,
            text=f"　{self.item_info['identifier']}　·　{self.item_info['types'] or '无类型'}",
            style="Sub.TLabel",
        ).pack(side="left")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self._build_basic_tab()
        if self.container:
            self._build_container_tab()
        self._build_tag_tab()
        if self.seed:
            self._build_seed_tab()
        if self.animal:
            self._build_animal_tab()

        bar = ttk.Frame(self, padding=(14, 0, 14, 12))
        bar.pack(fill="x")
        self.status = ttk.Label(bar, text="", style="Sub.TLabel")
        self.status.pack(side="left")
        ttk.Button(bar, text="写入暂存", style="Accent.TButton", command=self._apply).pack(side="right")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="还原为原值", command=self._reset).pack(side="right", padx=(0, 8))

    def _build_basic_tab(self) -> None:
        tab = ScrollFrame(self.nb)
        self.nb.add(tab, text="基本属性")
        root = tab.inner
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        left = ttk.Frame(root, padding=(4, 8, 16, 8))
        left.grid(row=0, column=0, sticky="new")
        right = ttk.Frame(root, padding=(0, 8, 4, 8))
        right.grid(row=0, column=1, sticky="new")

        self._section(left, "基本")
        for key, label, kind in itemprops.BASIC_FIELDS:
            self._scalar_row(left, label, kind, self.basic.get(key), self.basic_vars, key, tab)

        self._section(left, "文本")
        for key, label in itemprops.TEXT_FIELDS:
            ttk.Label(left, text=label).pack(anchor="w", pady=(6, 2))
            widget = tk.Text(left, height=3, wrap="word", width=46, font=("Microsoft YaHei UI", 9))
            widget.insert("1.0", self.texts.get(key, ""))
            widget.pack(fill="x")
            self.text_widgets[key] = widget
            tab.bind_wheel(widget)

        self._section(right, "贴图与类型")
        ttk.Label(right, text="类型标签（逗号分隔）").pack(anchor="w", pady=(6, 2))
        ttk.Entry(right, textvariable=self.types_var, width=40).pack(fill="x")

        for shape_key, shape_label in itemprops.SHAPES:
            self._section(right, shape_label)
            grid = ttk.Frame(right)
            grid.pack(fill="x", pady=(4, 0))
            variables = self.shape_vars.setdefault(shape_key, {})
            for i, (field, label, kind) in enumerate(itemprops.SHAPE_FIELDS):
                cell = ttk.Frame(grid)
                cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 10), pady=3)
                ttk.Label(cell, text=label, width=5).pack(side="left")
                value = self.shapes.get(shape_key, {}).get(field, 0 if kind != "bool" else False)
                if kind == "bool":
                    var = tk.BooleanVar(value=bool(value))
                    ttk.Checkbutton(cell, variable=var).pack(side="left")
                else:
                    var = tk.StringVar(value=str(value))
                    ttk.Entry(cell, textvariable=var, width=7, justify="right").pack(side="left")
                variables[field] = var

        note = ttk.Label(
            root,
            text=(
                "提示：宽/高改动后，格子里那串「data」掩码会按新尺寸自动重建。\n"
                "「当前形状」的 X/Y 决定物品在背包格子里的位置。"
            ),
            style="Sub.TLabel",
            justify="left",
        )
        note.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 10))

    def _section(self, parent, title: str) -> None:
        ttk.Label(parent, text=title, style="H1.TLabel").pack(anchor="w", pady=(10, 4))
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 4))

    def _scalar_row(self, parent, label: str, kind: str, value, store: dict, key: str, scroll) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=18).pack(side="left")
        if kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            widget = ttk.Checkbutton(row, variable=var)
            widget.pack(side="left")
            store[key] = var
        else:
            var = tk.StringVar(value="" if value is None else str(value))
            widget = ttk.Entry(row, textvariable=var, width=30 if kind == "str" else 14)
            widget.pack(side="left")
            store[key] = var
        scroll.bind_wheel(widget)

    # -- 牲畜基因 ---------------------------------------------------------

    def _build_animal_tab(self) -> None:
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="牲畜基因")
        scroller = ScrollFrame(shell)
        scroller.pack(fill="both", expand=True)
        outer = ttk.Frame(scroller.inner, padding=(12, 10))
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text=(
                "每项基因有两个等位基因（A/B），实际生效的「表达值」= (A+B)/2——"
                "和游戏的 GeneticHelper.GetExpressedValue 一致。繁殖时后代从父母各拿一个，"
                "15% 概率变异。改完 A/B 后表达值会自动按平均重算。"
            ),
            style="Sub.TLabel",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        genes_box = ttk.LabelFrame(outer, text=" 基因（等位基因 A / B） ", padding=(10, 8))
        genes_box.pack(fill="x")
        head = ttk.Frame(genes_box)
        head.pack(fill="x", pady=(0, 2))
        ttk.Label(head, text="基因", width=12, style="H1.TLabel").pack(side="left")
        ttk.Label(head, text="等位基因 A", width=12, style="H1.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(head, text="等位基因 B", width=12, style="H1.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(head, text="表达值", width=12, style="H1.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(head, text="物种基础", width=10, style="H1.TLabel").pack(side="left", padx=(8, 0))

        self.animal_gene_vars: dict[str, dict] = {}
        for gene in self.animal["genes"]:
            row = ttk.Frame(genes_box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=gene["label"], width=12).pack(side="left")
            var_a = tk.StringVar(value=fmt_number(gene["a"]))
            var_b = tk.StringVar(value=fmt_number(gene["b"]))
            ttk.Entry(row, textvariable=var_a, width=12, justify="right").pack(side="left", padx=(8, 0))
            ttk.Entry(row, textvariable=var_b, width=12, justify="right").pack(side="left", padx=(8, 0))
            shown = tk.StringVar(value=fmt_number(gene["expressed"]))
            ttk.Label(row, textvariable=shown, width=12, anchor="e").pack(side="left", padx=(8, 0))
            ttk.Label(row, text=fmt_number(gene["base"]), width=10, style="Sub.TLabel").pack(
                side="left", padx=(8, 0)
            )
            var_a.trace_add("write", lambda *_a, g=gene["id"]: self._refresh_gene_expression(g))
            var_b.trace_add("write", lambda *_a, g=gene["id"]: self._refresh_gene_expression(g))
            self.animal_gene_vars[gene["id"]] = {
                "a": var_a, "b": var_b, "shown": shown, "label": gene["label"],
                "base": gene["base"], "partner_a": gene["partner_a"], "partner_b": gene["partner_b"],
            }

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="等位基因全部拉满", command=self._animal_max).pack(side="left")
        ttk.Button(bar, text="恢复物种基础值", command=self._animal_default).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="A = B（纯合）", command=self._animal_homozygous).pack(side="left", padx=(8, 0))

        stats_box = ttk.LabelFrame(outer, text=" 其他属性 ", padding=(10, 8))
        stats_box.pack(fill="x", pady=(10, 0))
        self.animal_stat_vars: dict[str, tk.StringVar] = {}
        fields = [f for f in animal_dna.STAT_FIELDS]
        for index, (tag, label, kind) in enumerate(fields):
            cell = ttk.Frame(stats_box)
            cell.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 22), pady=3)
            ttk.Label(cell, text=label, width=14).pack(side="left")
            value = self.animal["stats"].get(tag)
            var = tk.StringVar(value="" if value is None else fmt_number(value))
            if kind == "str":
                ttk.Combobox(
                    cell, textvariable=var, width=12, state="normal",
                    values=("MALE", "FEMALE"),
                ).pack(side="left")
            else:
                ttk.Entry(cell, textvariable=var, width=14, justify="right").pack(side="left")
            self.animal_stat_vars[tag] = var

    def _refresh_gene_expression(self, gene_id: str) -> None:
        entry = self.animal_gene_vars.get(gene_id)
        if not entry:
            return
        try:
            a = float(entry["a"].get().replace(",", "") or 0)
            b = float(entry["b"].get().replace(",", "") or 0)
        except ValueError:
            entry["shown"].set("?")
            return
        entry["shown"].set(fmt_number((a + b) / 2))

    def _animal_max(self) -> None:
        for gene_id, entry in self.animal_gene_vars.items():
            entry["a"].set("100")
            entry["b"].set("100")
            self._refresh_gene_expression(gene_id)

    def _animal_default(self) -> None:
        for gene_id, entry in self.animal_gene_vars.items():
            entry["a"].set(fmt_number(entry["base"]))
            entry["b"].set(fmt_number(entry["base"]))
            self._refresh_gene_expression(gene_id)

    def _animal_homozygous(self) -> None:
        for gene_id, entry in self.animal_gene_vars.items():
            try:
                a = float(entry["a"].get().replace(",", "") or 0)
            except ValueError:
                continue
            entry["b"].set(fmt_number(a))
            self._refresh_gene_expression(gene_id)

    def _animal_values(self):
        genes = {}
        for gene_id, entry in self.animal_gene_vars.items():
            try:
                a = float(entry["a"].get().replace(",", "") or 0)
                b = float(entry["b"].get().replace(",", "") or 0)
            except ValueError:
                raise ValueError(entry["label"])
            genes[gene_id] = {
                "a": max(0.0, a), "b": max(0.0, b),
                "partner_a": entry["partner_a"], "partner_b": entry["partner_b"],
            }
        stats = {}
        for tag, var in self.animal_stat_vars.items():
            text = var.get().strip()
            if text == "":
                continue
            kind = dict((t, k) for t, _l, k in animal_dna.STAT_FIELDS)[tag]
            if kind == "str":
                stats[tag] = text
                continue
            try:
                stats[tag] = int(float(text.replace(",", ""))) if kind == "int" else float(text.replace(",", ""))
            except ValueError:
                raise ValueError(tag)
        return genes, stats

    # -- seed DNA --------------------------------------------------------

    def _build_seed_tab(self) -> None:
        shell = ttk.Frame(self.nb)
        self.nb.add(shell, text="种子 DNA")
        scroller = ScrollFrame(shell)
        scroller.pack(fill="both", expand=True)
        outer = ttk.Frame(scroller.inner, padding=(12, 10))
        outer.pack(fill="both", expand=True)

        stats_box = ttk.LabelFrame(outer, text=" 基因属性 ", padding=(10, 8))
        stats_box.pack(fill="x")
        self.seed_stat_vars: dict[str, tk.StringVar] = {}
        for i, (tag, label, kind) in enumerate(seed_dna.STAT_FIELDS):
            cell = ttk.Frame(stats_box)
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 22), pady=3)
            ttk.Label(cell, text=label, width=10).pack(side="left")
            var = tk.StringVar(value=str(self.seed["stats"].get(tag) or ""))
            ttk.Entry(cell, textvariable=var, width=20 if kind == "str" else 8,
                      justify="left" if kind == "str" else "right").pack(side="left")
            self.seed_stat_vars[tag] = var

        traits_box = ttk.LabelFrame(outer, text=" 性状（勾选 = 表达，数字 = 等级 0-10） ", padding=(10, 8))
        traits_box.pack(fill="both", expand=True, pady=(10, 0))

        self.seed_trait_vars: dict[str, tuple[tk.BooleanVar, tk.StringVar]] = {}
        expressed = {t["key"]: t for t in self.seed["traits"]}
        for i, name in enumerate(seed_dna.TRAITS):
            label, hint = seed_dna.TRAITS[name]
            cell = ttk.Frame(traits_box)
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 18), pady=3)
            on = tk.BooleanVar(value=name in expressed)
            ttk.Checkbutton(cell, variable=on, text=label, width=11).pack(side="left")
            level = tk.StringVar(value=str(expressed.get(name, {}).get("level", 5) or 5))
            ttk.Spinbox(cell, from_=1, to=seed_dna.MAX_LEVEL, textvariable=level, width=4).pack(side="left")
            ttk.Label(cell, text=hint, style="Sub.TLabel", width=12).pack(side="left", padx=(6, 0))
            self.seed_trait_vars[name] = (on, level)

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="全部满级 10", command=self._seed_all_max).pack(side="left")
        ttk.Button(bar, text="全部清零", command=self._seed_all_zero).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="恢复读取时的状态", command=self._seed_reset).pack(side="left", padx=(8, 0))
        ttk.Label(
            bar,
            text="新增性状会真的往种子里加一条基因标签，去掉勾则会删掉那条标签",
            style="Sub.TLabel",
        ).pack(side="left", padx=(14, 0))

    def _seed_all_max(self) -> None:
        for on, level in self.seed_trait_vars.values():
            on.set(True)
            level.set(str(seed_dna.MAX_LEVEL))

    def _seed_all_zero(self) -> None:
        for on, _level in self.seed_trait_vars.values():
            on.set(False)

    def _seed_reset(self) -> None:
        expressed = {t["key"]: t for t in self.seed["traits"]}
        for name, (on, level) in self.seed_trait_vars.items():
            on.set(name in expressed)
            level.set(str(expressed.get(name, {}).get("level", 5) or 5))
        for tag, var in self.seed_stat_vars.items():
            var.set(str(self.seed["stats"].get(tag) or ""))

    def _seed_values(self):
        stats = {}
        problems = []
        for tag, label, kind in seed_dna.STAT_FIELDS:
            raw = str(self.seed_stat_vars[tag].get()).strip()
            if kind == "str":
                stats[tag] = raw
                continue
            if not raw:
                stats[tag] = 0
                continue
            try:
                stats[tag] = int(float(raw))
            except ValueError:
                problems.append(label)
        levels = {}
        for name, (on, level) in self.seed_trait_vars.items():
            if not on.get():
                levels[name] = 0
                continue
            try:
                levels[name] = max(1, min(seed_dna.MAX_LEVEL, int(float(level.get() or 1))))
            except ValueError:
                problems.append(seed_dna.TRAITS[name][0])
        return stats, levels, problems

    # -- contents --------------------------------------------------------

    def _build_container_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=(10, 8))
        self.nb.add(tab, text="容器 / 内容物")
        info = self.container

        head = ttk.Frame(tab)
        head.pack(fill="x", pady=(0, 6))

        self.cap_var = tk.StringVar()
        self.temp_var = tk.StringVar()
        self.purity_var = tk.StringVar()
        for var, label, key, width in (
            (self.cap_var, "容量", "capacity", 12),
            (self.temp_var, "温度 ℃", "temperature", 8),
            (self.purity_var, "纯度 %", "purity", 8),
        ):
            item = info.get(key)
            if item is None:
                continue
            cell = ttk.Frame(head)
            cell.pack(side="left", padx=(0, 16))
            ttk.Label(cell, text=label).pack(side="left", padx=(0, 4))
            var.set(fmt_number(item["value"]))
            ttk.Entry(cell, textvariable=var, width=width, justify="right").pack(side="left")

        self.fill_var = tk.StringVar()
        ttk.Label(head, textvariable=self.fill_var, style="Sub.TLabel").pack(side="left", padx=(8, 0))

        columns = ("part", "amount", "readable", "share")
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True)
        self.part_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse", height=11)
        for col, text, width, anchor_ in (
            ("part", "成分", 220, "w"),
            ("amount", "含量（微升）", 140, "e"),
            ("readable", "可读", 120, "e"),
            ("share", "占比", 80, "e"),
        ):
            self.part_tree.heading(col, text=text)
            self.part_tree.column(col, width=width, anchor=anchor_)
        self.part_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.part_tree.yview)
        scroll.pack(side="right", fill="y")
        self.part_tree.configure(yscrollcommand=scroll.set)
        self.part_tree.bind("<<TreeviewSelect>>", self._on_part_select)

        self.only_nonzero = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            tab, text="只看非零成分", variable=self.only_nonzero, command=self._fill_part_tree
        ).pack(anchor="w", pady=(6, 2))

        edit = ttk.Frame(tab)
        edit.pack(fill="x", pady=(2, 4))
        ttk.Label(edit, text="选中成分的含量 →").pack(side="left")
        self.part_amount_var = tk.StringVar()
        ttk.Entry(edit, textvariable=self.part_amount_var, width=14, justify="right").pack(
            side="left", padx=(6, 6)
        )
        ttk.Button(edit, text="应用", command=self._apply_part_amount).pack(side="left")
        self.part_note = ttk.Label(edit, text="", style="Sub.TLabel")
        self.part_note.pack(side="left", padx=(12, 0))

        quick = ttk.Frame(tab)
        quick.pack(fill="x", pady=(4, 0))
        ttk.Button(quick, text="清空内容物", command=self._contents_clear).pack(side="left")
        ttk.Button(quick, text="按容量灌满", command=self._contents_fill).pack(side="left", padx=(8, 0))
        ttk.Button(quick, text="灌满纯水", command=self._contents_water).pack(side="left", padx=(8, 0))
        ttk.Button(quick, text="只留选中成分 99.5%", command=self._contents_pure).pack(side="left", padx=(8, 0))
        ttk.Label(
            quick, text="容量单位是微升（25 毫升 = 25000）", style="Sub.TLabel"
        ).pack(side="left", padx=(14, 0))

        self._fill_part_tree()

    def _effective_amount(self, part: dict) -> int:
        edits = self.tag_edits.get(part["index"], {})
        return int(edits.get("valueInt", part["amount"]))

    def _effective_capacity(self) -> int:
        item = (self.container or {}).get("capacity")
        if not item:
            return 0
        raw = self.cap_var.get() if hasattr(self, "cap_var") else ""
        try:
            return int(float(str(raw).strip() or 0))
        except ValueError:
            return int(item["value"])

    def _fill_part_tree(self) -> None:
        if not hasattr(self, "part_tree"):
            return
        info = self.container
        total = sum(self._effective_amount(p) for p in info["parts"])
        capacity = self._effective_capacity()
        self.part_tree.delete(*self.part_tree.get_children())
        for part in info["parts"]:
            amount = self._effective_amount(part)
            if self.only_nonzero.get() and amount == 0:
                continue
            share = f"{amount / total * 100:.1f}%" if total else "—"
            self.part_tree.insert(
                "", "end", iid=str(part["index"]),
                values=(part["label"], amount, _volume(amount), share),
            )
        if capacity:
            percent = total / capacity * 100 if capacity else 0
            self.fill_var.set(f"合计 {_volume(total)} / 容量 {_volume(capacity)}（{percent:.1f}%）")
        else:
            self.fill_var.set(f"合计 {_volume(total)}")

    def _on_part_select(self, _event=None) -> None:
        sel = self.part_tree.selection()
        if not sel:
            return
        index = int(sel[0])
        for part in self.container["parts"]:
            if part["index"] == index:
                self.part_amount_var.set(str(self._effective_amount(part)))
                self.part_note.configure(text=f"正在编辑：{part['label']}")
                return

    def _apply_part_amount(self) -> None:
        sel = self.part_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在上面的列表里选一个成分。", parent=self)
            return
        index = int(sel[0])
        try:
            amount = int(float(self.part_amount_var.get().strip() or 0))
        except ValueError:
            messagebox.showwarning("数值不对", "含量要填整数（微升）。", parent=self)
            return
        if amount < 0:
            messagebox.showwarning("数值不对", "含量不能是负数。", parent=self)
            return
        self.tag_edits.setdefault(index, {})["valueInt"] = amount
        self._fill_part_tree()
        if self.part_tree.exists(str(index)):
            self.part_tree.selection_set(str(index))
        self.part_note.configure(text="已记录，点右下角「写入暂存」生效")

    def _contents_clear(self) -> None:
        for part in self.container["parts"]:
            self.tag_edits.setdefault(part["index"], {})["valueInt"] = 0
        self._fill_part_tree()

    def _contents_fill(self) -> None:
        capacity = self._effective_capacity()
        if not capacity:
            messagebox.showinfo("提示", "这件物品没有容量信息。", parent=self)
            return
        parts = self.container["parts"]
        total = sum(self._effective_amount(p) for p in parts)
        if total <= 0:
            self._contents_water()
            return
        if total == capacity:
            return
        scale = capacity / total
        for part in parts:
            self.tag_edits.setdefault(part["index"], {})["valueInt"] = int(
                round(self._effective_amount(part) * scale)
            )
        self._fill_part_tree()

    def _contents_pure(self) -> None:
        """Keep only the selected component and fill it to 99.5% of capacity."""
        sel = self.part_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个成分，再点这个。", parent=self)
            return
        keep = int(sel[0])
        capacity = self._effective_capacity()
        if not capacity:
            messagebox.showinfo("提示", "这件物品没有容量信息。", parent=self)
            return
        amount = int(capacity * 0.995)
        for part in self.container["parts"]:
            self.tag_edits.setdefault(part["index"], {})["valueInt"] = (
                amount if part["index"] == keep else 0
            )
        self._fill_part_tree()
        self.part_note.configure(text=f"已设为单一成分 {_volume(amount)}，点「写入暂存」生效")

    def _contents_water(self) -> None:
        capacity = self._effective_capacity()
        if not capacity:
            messagebox.showinfo("提示", "这件物品没有容量信息。", parent=self)
            return
        water_key = itemprops.PART_PREFIX + itemprops.WATER_PART
        for part in self.container["parts"]:
            value = capacity if part["key"] == water_key else 0
            self.tag_edits.setdefault(part["index"], {})["valueInt"] = value
        self._fill_part_tree()

    def _build_tag_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=(10, 8))
        self.nb.add(tab, text="附加标签（_values）")

        ttk.Label(
            tab,
            text=(
                "每件物品的行为由这些标签决定（耐久、纯度、违禁等级、卡路里、买入价……）。"
                "「标识」是标签本身的名字，不在这里改；其余字段都能改。"
            ),
            style="Sub.TLabel",
            wraplength=880,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        columns = ("identifier", "enabled", "int", "float", "long", "double", "bool", "str")
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True)
        self.tag_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse", height=9)
        for col, text, width, anchor in (
            ("identifier", "标签", 240, "w"),
            ("enabled", "启用", 70, "center"),
            ("int", "Int", 90, "e"),
            ("float", "Float", 90, "e"),
            ("long", "Long", 90, "e"),
            ("double", "Double", 90, "e"),
            ("bool", "Bool", 70, "center"),
            ("str", "字符串", 150, "w"),
        ):
            self.tag_tree.heading(col, text=text)
            self.tag_tree.column(col, width=width, anchor=anchor)
        self.tag_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tag_tree.yview)
        scroll.pack(side="right", fill="y")
        self.tag_tree.configure(yscrollcommand=scroll.set)
        self.tag_tree.bind("<<TreeviewSelect>>", self._on_tag_select)

        self._fill_tag_tree()

        form = ttk.LabelFrame(tab, text=" 选中标签的字段 ", padding=(10, 8))
        form.pack(fill="x", pady=(8, 0))
        self.tag_vars: dict[str, object] = {}
        for i, (field, label, kind) in enumerate(itemprops.TAG_FIELDS):
            cell = ttk.Frame(form)
            cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 14), pady=4)
            ttk.Label(cell, text=label, width=8).pack(side="left")
            init = 0.0 if kind == "float" else 0
            if kind == "bool":
                var = tk.BooleanVar(value=False)
                ttk.Checkbutton(cell, variable=var).pack(side="left")
            elif kind == "str":
                var = tk.StringVar(value="")
                ttk.Entry(cell, textvariable=var, width=14).pack(side="left")
            else:
                var = tk.StringVar(value=str(init))
                ttk.Entry(cell, textvariable=var, width=8, justify="right").pack(side="left")
            self.tag_vars[field] = var

        bar = ttk.Frame(form)
        bar.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(bar, text="应用到选中标签", command=self._apply_tag_form).pack(side="left")
        self.tag_note = ttk.Label(bar, text="", style="Sub.TLabel")
        self.tag_note.pack(side="left", padx=(12, 0))

    def _fill_tag_tree(self) -> None:
        self.tag_tree.delete(*self.tag_tree.get_children())
        for tag in self.tags:
            index = tag["index"]
            edited = self.tag_edits.get(index, {})
            merged = {**tag, **edited}
            self.tag_tree.insert(
                "", "end", iid=str(index),
                values=(
                    merged.get("identifier", ""),
                    "✔" if merged.get("valueEnabled") else "✘",
                    merged.get("valueInt", 0),
                    merged.get("valueFloat", 0.0),
                    merged.get("valueLong", 0),
                    merged.get("valueDouble", 0.0),
                    "✔" if merged.get("valueBool") else "✘",
                    merged.get("internalValueString", ""),
                ),
            )

    # -- tag editing ---------------------------------------------------------

    def _on_tag_select(self, _event=None) -> None:
        sel = self.tag_tree.selection()
        if not sel:
            return
        index = int(sel[0])
        tag = dict(self.tags[index])
        tag.update(self.tag_edits.get(index, {}))
        for field, _label, kind in itemprops.TAG_FIELDS:
            var = self.tag_vars.get(field)
            if var is None:
                continue
            value = tag.get(field, 0.0 if kind == "float" else ("" if kind == "str" else 0))
            if kind == "bool":
                var.set(bool(value))
            else:
                var.set(str(value))
        self.tag_note.configure(text=f"正在编辑：{tag.get('identifier', '')}")

    def _apply_tag_form(self) -> None:
        sel = self.tag_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在上面的列表里选一个标签。", parent=self)
            return
        index = int(sel[0])
        values = {}
        problems = []
        for field, label, kind in itemprops.TAG_FIELDS:
            var = self.tag_vars.get(field)
            if var is None:
                continue
            raw = var.get()
            if kind == "bool":
                values[field] = bool(raw)
                continue
            try:
                if kind == "int":
                    values[field] = int(float(str(raw).strip() or 0))
                elif kind == "float":
                    values[field] = float(str(raw).strip() or 0)
                else:
                    values[field] = str(raw)
            except ValueError:
                problems.append(label)
        if problems:
            messagebox.showwarning("数值不对", "这些字段不是数字：" + "、".join(problems), parent=self)
            return
        self.tag_edits.setdefault(index, {}).update(values)
        self._fill_tag_tree()
        self.tag_tree.selection_set(str(index))
        self.tag_note.configure(text="已记录，点右下角「写入暂存」生效")

    # -- collect + apply -----------------------------------------------------

    def _collect(self):
        problems = []
        basic = {}
        for key, label, kind in itemprops.BASIC_FIELDS:
            var = self.basic_vars.get(key)
            if var is None:
                continue
            if kind == "bool":
                basic[key] = bool(var.get())
                continue
            raw = str(var.get())
            try:
                if kind == "int":
                    basic[key] = int(float(raw.strip() or 0))
                elif kind == "float":
                    basic[key] = float(raw.strip() or 0)
                else:
                    basic[key] = raw
            except ValueError:
                problems.append(label)

        shapes = {}
        for shape_key, _label in itemprops.SHAPES:
            values = {}
            for field, flabel, kind in itemprops.SHAPE_FIELDS:
                var = self.shape_vars.get(shape_key, {}).get(field)
                if var is None:
                    continue
                if kind == "bool":
                    values[field] = bool(var.get())
                    continue
                try:
                    values[field] = int(float(str(var.get()).strip() or 0))
                except ValueError:
                    problems.append(f"{shape_key}.{flabel}")
            shapes[shape_key] = values

        types = [t.strip() for t in self.types_var.get().split(",") if t.strip()]
        texts = {k: w.get("1.0", "end-1c") for k, w in self.text_widgets.items()}

        # capacity / temperature / purity live in the container tab
        if self.container:
            for key, var, label in (
                ("capacity", getattr(self, "cap_var", None), "容量"),
                ("temperature", getattr(self, "temp_var", None), "温度"),
                ("purity", getattr(self, "purity_var", None), "纯度"),
            ):
                entry = self.container.get(key)
                if entry is None or var is None:
                    continue
                try:
                    raw = str(var.get()).strip() or "0"
                    value = float(raw) if entry["field"] == "valueDouble" else int(float(raw))
                except ValueError:
                    problems.append(label)
                    continue
                self.tag_edits.setdefault(entry["index"], {})[entry["field"]] = value

        return basic, texts, shapes, types, problems

    def _reset(self) -> None:
        if not messagebox.askyesno("还原", "丢弃这个窗口里的所有改动？", parent=self):
            return
        self.destroy()
        ItemEditor(self.master, self.editor, self.inv_key, self.index, self.on_saved)

    def _apply(self) -> None:
        basic, texts, shapes, types, problems = self._collect()
        seed_stats, seed_levels = {}, {}
        animal_genes, animal_stats = {}, {}
        if self.seed:
            seed_stats, seed_levels, seed_problems = self._seed_values()
            problems.extend(seed_problems)
        if self.animal:
            try:
                animal_genes, animal_stats = self._animal_values()
            except ValueError as exc:
                problems.append(f"牲畜基因：{exc} 填得不对")
        if problems:
            messagebox.showwarning("这些字段填得不对", "、".join(problems), parent=self)
            return

        def apply_all(doc, node):
            if self.seed:
                # seeds go through the DNA writer, which also folds in the
                # generic tag edits (it rewrites the whole _values array)
                itemprops.apply_edits(doc, node, basic, texts, shapes, types, {})
                seed_dna.apply_dna(doc, node, seed_stats, seed_levels, self.tag_edits)
            elif self.animal:
                itemprops.apply_edits(doc, node, basic, texts, shapes, types, {})
                animal_dna.apply_animal(doc, node, animal_genes, animal_stats)
            else:
                itemprops.apply_edits(doc, node, basic, texts, shapes, types, self.tag_edits)

        try:
            self.editor.inventory_update_item(
                self.inv_key, self.index, apply_all, label="编辑物品属性"
            )
        except Exception as exc:
            messagebox.showerror("写入暂存失败", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()
