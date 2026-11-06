"""「自定义模组」工坊：形状、词条、基础属性都能自己定。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import es3json
import itemprops
import modules
import itemeditor
import savegame

CELL = 36


class ModuleWorkshop(tk.Toplevel):
    """Build a module by cloning a real one and rewriting every field."""

    def __init__(self, master, editor: savegame.SlotEditor, catalog, on_created=None):
        super().__init__(master)
        self.editor = editor
        self.catalog = catalog
        self.on_created = on_created
        self.templates: list[tuple[str, str, dict]] = []
        self.mask: list[int] = []
        self.width = 2
        self.height = 2
        self._cells: list[tuple[int, int, int]] = []

        self.title("自定义模组")
        self.transient(master)
        self.minsize(900, 620)

        self._collect_templates()
        self._build()
        self._load_template(0)
        itemeditor.present_dialog(self, master, 1000, 700)

    # -- data ---------------------------------------------------------------

    def _collect_templates(self) -> None:
        seen = set()
        for key, _label, _count in self.editor.inventories():
            doc = self.editor.inventory(key)
            for node in doc.root.get("saveItems").items:
                if not modules.is_module(node):
                    continue
                ident = node.get("identifier").value
                if ident in seen:
                    continue
                info = modules.read_module(node)
                label = f"{info['name']}（{modules.type_label(info['type'])}·{info['tier']}级）"
                self.templates.append((label, doc.slice(node), info))
                seen.add(ident)
        # catalogue modules extend the list with things not in the bag right now
        if self.catalog is not None:
            for entry in self.catalog.all():
                if entry.identifier in seen or "MODULE_TAG" not in entry.template:
                    continue
                try:
                    node = es3json.Document(entry.template).root
                except es3json.ES3Error:
                    continue
                if not modules.is_module(node):
                    continue
                info = modules.read_module(node)
                label = f"{info['name']}（{modules.type_label(info['type'])}·{info['tier']}级）· 图鉴"
                self.templates.append((label, entry.template, info))
                seen.add(entry.identifier)

    # -- layout -------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self, padding=(14, 10, 14, 4))
        head.pack(fill="x")
        ttk.Label(head, text="模板", width=8).pack(side="left")
        self.template_var = tk.StringVar()
        self.template_combo = ttk.Combobox(
            head, textvariable=self.template_var, state="readonly",
            values=[t[0] for t in self.templates], width=52,
        )
        self.template_combo.pack(side="left")
        self.template_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_template_change())
        ttk.Label(head, text="克隆真实模组", style="Sub.TLabel").pack(side="left", padx=(10, 0))

        body = ttk.Frame(self, padding=(14, 6))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))

        self._build_basic(left)
        self._build_effects(left)
        self._build_shape(right)
        self._build_footer()

    def _row(self, parent, label: str, width: int = 16):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=width).pack(side="left")
        return row

    def _build_basic(self, parent) -> None:
        box = ttk.LabelFrame(parent, text=" 基础属性 ", padding=(12, 8))
        box.pack(fill="x")

        row = self._row(box, "名称")
        self.name_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.name_var, width=34).pack(side="left", fill="x", expand=True)

        row = self._row(box, "估值")
        self.value_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.value_var, width=10, justify="right").pack(side="left")

        row = self._row(box, "等级")
        self.tier_var = tk.StringVar()
        ttk.Spinbox(row, from_=1, to=20, textvariable=self.tier_var, width=6).pack(side="left")
        ttk.Label(row, text="等级", style="Sub.TLabel").pack(side="left", padx=(8, 0))

        row = self._row(box, "类型")
        self.type_var = tk.StringVar()
        self.type_combo = ttk.Combobox(
            row, textvariable=self.type_var, state="readonly", width=16,
            values=[label for _tag, label, _n in modules.MODULE_TYPES],
        )
        self.type_combo.pack(side="left")
        self.type_default_name = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            box, text="换类型时自动改名", variable=self.type_default_name
        ).pack(anchor="w", pady=(4, 0))

    def _build_effects(self, parent) -> None:
        box = ttk.LabelFrame(parent, text=" 词条 ", padding=(12, 8))
        box.pack(fill="x", pady=(10, 0))

        self.effect_vars: dict[str, tk.StringVar] = {}
        for tag, label in modules.EFFECT_FIELDS:
            row = self._row(box, label)
            var = tk.StringVar(value="0")
            ttk.Entry(row, textvariable=var, width=8, justify="right").pack(side="left")
            ttk.Label(row, text="%", style="Sub.TLabel").pack(side="left", padx=(4, 0))
            self.effect_vars[tag] = var

        ttk.Label(
            box, text="正数加成、负数惩罚。", style="Sub.TLabel"
        ).pack(anchor="w", pady=(2, 6))

        row = self._row(box, "附加效果")
        self.extra_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.extra_var, width=40).pack(side="left", fill="x", expand=True)
        ttk.Label(
            box,
            text="游戏里显示在模组说明里的那行字，比如「仅回收溶剂」，留空则不显示。",
            style="Sub.TLabel",
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def _build_shape(self, parent) -> None:
        box = ttk.LabelFrame(parent, text=" 形状 ", padding=(12, 8))
        box.pack(fill="both", expand=True)

        row = self._row(box, "格子", width=6)
        ttk.Label(row, text="宽").pack(side="left")
        self.w_var = tk.StringVar(value="2")
        w_spin = ttk.Spinbox(row, from_=1, to=modules.MAX_SIDE, width=4,
                             textvariable=self.w_var, command=self._on_size_change)
        w_spin.pack(side="left", padx=(2, 8))
        ttk.Label(row, text="高").pack(side="left")
        self.h_var = tk.StringVar(value="2")
        h_spin = ttk.Spinbox(row, from_=1, to=modules.MAX_SIDE, width=4,
                             textvariable=self.h_var, command=self._on_size_change)
        h_spin.pack(side="left", padx=(2, 0))
        w_spin.bind("<FocusOut>", lambda _e: self._on_size_change())
        h_spin.bind("<FocusOut>", lambda _e: self._on_size_change())

        self.canvas = tk.Canvas(box, width=CELL * 4, height=CELL * 4, background="#fafafa",
                                highlightthickness=1, highlightbackground="#cccccc")
        self.canvas.pack(pady=(8, 6))
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        ttk.Label(
            box,
            text="点格子涂掉/涂上，做出 L 形、T 形这类异形模组。",
            style="Sub.TLabel",
            wraplength=240,
            justify="left",
        ).pack(anchor="w")

        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="全选", command=self._mask_all).pack(side="left")
        ttk.Button(buttons, text="清空", command=self._mask_none).pack(side="left", padx=(6, 0))

    def _build_footer(self) -> None:
        bar = ttk.Frame(self, padding=(14, 6, 14, 12))
        bar.pack(fill="x")
        ttk.Label(bar, text="放入", style="Sub.TLabel").pack(side="left")
        self.bag_var = tk.StringVar()
        self.bag_map = {label: key for key, label, _count in self.editor.inventories()}
        self.bag_combo = ttk.Combobox(
            bar, textvariable=self.bag_var, state="readonly", width=20,
            values=list(self.bag_map),
        )
        self.bag_combo.pack(side="left", padx=(6, 12))
        if self.bag_map:
            self.bag_combo.current(0)

        ttk.Button(bar, text="生成并加入背包", style="Accent.TButton", command=self._create).pack(
            side="right"
        )
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))
        self.status = ttk.Label(bar, text="", style="Sub.TLabel")
        self.status.pack(side="left", padx=(10, 0))

    # -- state --------------------------------------------------------------

    def _current_template(self) -> tuple[str, str, dict] | None:
        sel = self.template_combo.current()
        if not self.templates:
            return None
        return self.templates[max(0, sel)]

    def _load_template(self, index: int) -> None:
        if not self.templates:
            return
        self.template_combo.current(index)
        label, text, info = self.templates[index]
        self.name_var.set(info["name"])
        self.value_var.set(str(info["value"]))
        self.tier_var.set(str(info["tier"]))
        for i, (tag, _label, _n) in enumerate(modules.MODULE_TYPES):
            if tag == info["type"]:
                self.type_combo.current(i)
                break
        else:
            self.type_combo.current(0)
        self.type_var.set(modules.MODULE_TYPES[self.type_combo.current() or 0][1])

        for tag, _label in modules.EFFECT_FIELDS:
            self.effect_vars[tag].set(str(info["effects"].get(tag, 0)))
        self.extra_var.set(info.get("extra") or "")

        self.width = max(1, min(modules.MAX_SIDE, info["width"]))
        self.height = max(1, min(modules.MAX_SIDE, info["height"]))
        self.w_var.set(str(self.width))
        self.h_var.set(str(self.height))
        mask = list(info.get("mask") or [])
        if len(mask) != self.width * self.height:
            mask = [1] * (self.width * self.height)
        self.mask = mask
        self._draw_canvas()

    def _on_template_change(self) -> None:
        index = self.template_combo.current()
        if index >= 0:
            self._load_template(index)

    def _on_size_change(self) -> None:
        try:
            width = max(1, min(modules.MAX_SIDE, int(self.w_var.get())))
            height = max(1, min(modules.MAX_SIDE, int(self.h_var.get())))
        except ValueError:
            return
        if width == self.width and height == self.height:
            return
        old_w, old_h, old_mask = self.width, self.height, self.mask
        self.width, self.height = width, height
        # keep what fits, default new cells to filled
        self.mask = []
        for y in range(height):
            for x in range(width):
                if x < old_w and y < old_h:
                    self.mask.append(old_mask[y * old_w + x])
                else:
                    self.mask.append(1)
        self._draw_canvas()

    def _mask_all(self) -> None:
        self.mask = [1] * (self.width * self.height)
        self._draw_canvas()

    def _mask_none(self) -> None:
        self.mask = [0] * (self.width * self.height)
        self._draw_canvas()

    def _draw_canvas(self) -> None:
        self.canvas.delete("all")
        self._cells = []
        for y in range(self.height):
            for x in range(self.width):
                index = y * self.width + x
                on = 1 if index < len(self.mask) and self.mask[index] else 0
                fill = "#2f6f4f" if on else "#ffffff"
                outline = "#1d4a35" if on else "#bbbbbb"
                item = self.canvas.create_rectangle(
                    x * CELL + 2, y * CELL + 2, (x + 1) * CELL - 2, (y + 1) * CELL - 2,
                    fill=fill, outline=outline, width=2,
                )
                self._cells.append((item, x, y))
        self.canvas.configure(width=CELL * self.width, height=CELL * self.height)

    def _on_canvas_click(self, event) -> None:
        x = int(event.x // CELL)
        y = int(event.y // CELL)
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        index = y * self.width + x
        while len(self.mask) < self.width * self.height:
            self.mask.append(0)
        self.mask[index] = 0 if self.mask[index] else 1
        self._draw_canvas()

    # -- create -------------------------------------------------------------

    def _create(self) -> None:
        template = self._current_template()
        if template is None:
            messagebox.showwarning("没有模板", "当前存档里没有可用的模组模板，先买一个模组再回来。", parent=self)
            return
        _label, text, info = template

        try:
            value = int(float(self.value_var.get().strip() or 0))
            tier = int(float(self.tier_var.get().strip() or 1))
        except ValueError:
            messagebox.showwarning("数值不对", "估值和等级要填数字。", parent=self)
            return

        effects = {}
        for tag, label in modules.EFFECT_FIELDS:
            try:
                effects[tag] = int(float(self.effect_vars[tag].get().strip() or 0))
            except ValueError:
                messagebox.showwarning("数值不对", f"{label}要填数字（可负数）。", parent=self)
                return

        type_index = self.type_combo.current()
        type_tag = modules.MODULE_TYPES[max(0, type_index)][0]

        name = self.name_var.get().strip() or info["name"]
        if self.type_default_name.get() and type_index >= 0 and type_tag != info["type"]:
            name = f"{modules.MODULE_TYPES[max(0, type_index)][2]}·自定义"

        if not any(self.mask):
            messagebox.showwarning("形状是空的", "至少要留一个格子。", parent=self)
            return

        spec = {
            "name": name,
            "value": value,
            "tier": tier,
            "type": type_tag,
            "effects": effects,
            "extra": self.extra_var.get(),
            "width": self.width,
            "height": self.height,
            "mask": self.mask,
        }
        try:
            built = modules.build_module(text, spec)
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc), parent=self)
            return

        bag = self.bag_map.get(self.bag_var.get())
        if not bag:
            messagebox.showwarning("没有背包", "没有可放入的背包。", parent=self)
            return
        try:
            created = self.editor.inventory_add(bag, built)
        except Exception as exc:
            messagebox.showerror("加入背包失败", str(exc), parent=self)
            return

        if self.on_created:
            self.on_created()
        self.status.configure(text=f"已生成「{created}」，记得点「保存到游戏」")
        self.destroy()
