"""The gas sign window. One radius, one button shape, one type scale."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from boards import BOARDS

NIGHT_TOP = (8, 16, 34)
PAPER_RGB = NIGHT_TOP
SHEET_RGB = PAPER_RGB
INK_RGB = (232, 238, 245)
MUTED_RGB = (158, 176, 198)
LINE_RGB = (48, 74, 108)
FIELD_RGB = (8, 18, 38)
QUIET_RGB = (24, 42, 72)
QUIET_HOVER_RGB = (34, 56, 92)
ACCENT_RGB = (72, 148, 224)
SOLID_RGB = ACCENT_RGB
SOLID_HOVER_RGB = (96, 168, 236)
SOLID_TEXT = (244, 248, 252)
DISABLED_RGB = (22, 34, 52)

RADIUS = 8
CONTROL = 40


def _hex(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


PAPER = _hex(PAPER_RGB)
SHEET = _hex(SHEET_RGB)
INK = _hex(INK_RGB)
MUTED = _hex(MUTED_RGB)
LINE = _hex(LINE_RGB)
ACCENT = _hex(ACCENT_RGB)
FIELD = _hex(FIELD_RGB)
WHITE = FIELD

_FONTS: dict[int, ImageFont.ImageFont] = {}


def _face(size: int) -> ImageFont.ImageFont:
    cached = _FONTS.get(size)
    if cached is not None:
        return cached
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
    face = ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()
    _FONTS[size] = face
    return face


def _rounded(width: int, height: int, fill: tuple[int, int, int], outline: tuple[int, int, int] | None) -> Image.Image:
    scale = 3
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (scale, scale, width * scale - scale - 1, height * scale - scale - 1)
    draw.rounded_rectangle(
        box,
        radius=RADIUS * scale,
        fill=fill + (255,),
        outline=None if outline is None else outline + (255,),
        width=scale,
    )
    return image.resize((width, height), Image.Resampling.LANCZOS)


class Command(tk.Label):
    """Every button is this shape. Solid is ink. Quiet is a lined field."""

    def __init__(self, parent, text: str, command, kind: str = "solid") -> None:
        super().__init__(parent, bd=0, highlightthickness=0, bg=SHEET, padx=0, pady=0, cursor="hand2")
        self._text = text
        self._command = command
        self._kind = kind
        self._state = "normal"
        self._hover = False
        self._photo: ImageTk.PhotoImage | None = None
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._paint()

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if cnf is not None and not kwargs:
            return super().configure(cnf)
        changed = False
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            changed = True
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            changed = True
        if kwargs:
            super().configure(**kwargs)
        if changed:
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            self._paint()

    config = configure

    def _paint(self) -> None:
        scale = 3
        face = _face(13 * scale)
        width = max(96 * scale, int(face.getlength(self._text)) + 28 * scale)
        height = CONTROL * scale
        if self._state == "disabled":
            fill, ink, edge = DISABLED_RGB, MUTED_RGB, LINE_RGB
        elif self._kind == "solid":
            fill = SOLID_HOVER_RGB if self._hover else SOLID_RGB
            ink, edge = SOLID_TEXT, fill
        else:
            fill = QUIET_HOVER_RGB if self._hover else QUIET_RGB
            ink, edge = INK_RGB, LINE_RGB
        plate = Image.new("RGBA", (width, height), SHEET_RGB + (255,))
        ImageDraw.Draw(plate).rounded_rectangle(
            (scale, scale, width - scale - 1, height - scale - 1),
            radius=RADIUS * scale,
            fill=fill + (255,),
            outline=edge + (255,),
            width=scale,
        )
        ImageDraw.Draw(plate).text((width / 2, height / 2), self._text, font=face, fill=ink + (255,), anchor="mm")
        image = plate.resize((width // scale, CONTROL), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image)
        super().configure(image=self._photo)

    def _click(self, _event) -> None:
        if self._state != "disabled" and self._command is not None:
            self._command()

    def _enter(self, _event) -> None:
        if self._state == "disabled" or self._hover:
            return
        self._hover = True
        self._paint()

    def _leave(self, _event) -> None:
        if not self._hover:
            return
        self._hover = False
        self._paint()


class Field(tk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent, bg=SHEET, width=180, height=CONTROL, highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self._photo: ImageTk.PhotoImage | None = None
        self._focused = False
        self._size = (0, 0)
        self.chrome = tk.Label(self, bd=0, highlightthickness=0, bg=SHEET)
        self.chrome.place(x=0, y=0, relwidth=1, relheight=1)
        self.bind("<Configure>", self._redraw)

    def mount(self, widget: tk.Widget) -> None:
        widget.place(x=RADIUS, y=RADIUS, relwidth=1, width=-(RADIUS * 2), height=CONTROL - RADIUS * 2)
        self.chrome.lower(widget)

    def set_focus(self, focused: bool) -> None:
        self._focused = focused
        self._size = (0, 0)
        self._redraw()

    def _redraw(self, _event=None) -> None:
        width, height = self.winfo_width(), CONTROL
        if width < 20 or (width, height, self._focused) == self._size:
            return
        self._size = (width, height, self._focused)
        edge = ACCENT_RGB if self._focused else LINE_RGB
        plate = Image.new("RGB", (width, height), SHEET_RGB)
        sprite = _rounded(width, height, FIELD_RGB, edge)
        plate.paste(sprite, (0, 0), sprite)
        self._photo = ImageTk.PhotoImage(plate)
        self.chrome.configure(image=self._photo)
        self.chrome.lower()


def _style() -> None:
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Gas.TCombobox",
        fieldbackground=FIELD,
        background=FIELD,
        foreground=INK,
        arrowcolor=INK,
        bordercolor=FIELD,
        lightcolor=FIELD,
        darkcolor=FIELD,
        borderwidth=0,
        padding=0,
        relief="flat",
        font=("Segoe UI", 13),
    )
    style.map(
        "Gas.TCombobox",
        fieldbackground=[("readonly", FIELD)],
        foreground=[("readonly", INK)],
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", INK)],
    )
    style.configure(
        "Gas.TSpinbox",
        fieldbackground=FIELD,
        background=FIELD,
        foreground=INK,
        arrowcolor=INK,
        bordercolor=FIELD,
        lightcolor=FIELD,
        darkcolor=FIELD,
        borderwidth=0,
        padding=0,
        arrowsize=12,
        relief="flat",
        font=("Segoe UI", 13),
    )
    style.configure(
        "Gas.Horizontal.TScale",
        background=SHEET,
        troughcolor=_hex((12, 24, 44)),
        borderwidth=0,
        sliderthickness=12,
        sliderrelief="flat",
    )


def _text(parent, text: str, size: int, color: str = INK, variable=None) -> tk.Label:
    label = tk.Label(
        parent,
        text=text,
        textvariable=variable,
        fg=color,
        bg=SHEET,
        anchor="w",
        justify="left",
        font=("Segoe UI", size),
    )
    return label


def _entry(parent, variable) -> tuple[Field, tk.Entry]:
    shell = Field(parent)
    entry = tk.Entry(
        shell,
        textvariable=variable,
        relief="flat",
        bd=0,
        bg=WHITE,
        fg=INK,
        insertbackground=INK,
        highlightthickness=0,
        font=("Segoe UI", 13),
    )
    entry.bind("<FocusIn>", lambda _event: shell.set_focus(True))
    entry.bind("<FocusOut>", lambda _event: shell.set_focus(False))
    shell.mount(entry)
    return shell, entry


def _combo(parent, variable) -> tuple[Field, ttk.Combobox]:
    shell = Field(parent)
    combo = ttk.Combobox(shell, textvariable=variable, state="readonly", style="Gas.TCombobox", font=("Segoe UI", 13))
    shell.mount(combo)
    return shell, combo


def _minutes_entry(parent, variable) -> tk.Entry:
    entry = tk.Entry(
        parent,
        textvariable=variable,
        width=6,
        relief="flat",
        bd=0,
        bg=FIELD,
        fg=INK,
        insertbackground=ACCENT,
        highlightthickness=1,
        highlightbackground=_hex(LINE_RGB),
        highlightcolor=ACCENT,
        justify="center",
        font=("Segoe UI", 13),
    )
    return entry


def _heading(parent, title: str, version: str, title_var=None) -> None:
    row = tk.Frame(parent, bg=SHEET, highlightthickness=0, bd=0)
    row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
    row.grid_columnconfigure(0, weight=1)
    _text(row, "" if title_var is not None else title, 22, variable=title_var).grid(row=0, column=0, sticky="w")
    _text(row, version, 12, MUTED).grid(row=0, column=1, sticky="e")


def build(app) -> None:
    _style()
    app.root.configure(bg=PAPER)
    app._window_icon = ImageTk.PhotoImage(app._icon_image)
    app.root.iconphoto(True, app._window_icon)
    app.root.grid_rowconfigure(0, weight=1)
    app.root.grid_columnconfigure(0, weight=1)
    app.shell = tk.Frame(app.root, bg=PAPER, highlightthickness=0, bd=0)
    app.shell.grid(row=0, column=0, sticky="nsew")
    app.shell.grid_rowconfigure(0, weight=1)
    app.shell.grid_columnconfigure(0, weight=1)
    app._wraps: list[tk.Label] = []
    app._prices: list[tk.Label] = []
    app.wiring_page = _page(app, _wiring)
    app.upload_page = _page(app, _upload)
    app.wifi_page = _page(app, _wifi)
    app.price_page = _page(app, _prices)
    app.board_page = _page(app, _picker)
    apply_scale(app, 1100)


def _page(app, filler) -> tk.Frame:
    page = tk.Frame(app.shell, bg=PAPER, highlightthickness=0, bd=0)
    page.grid_rowconfigure(0, weight=1)
    page.grid_columnconfigure(0, weight=1)
    body = tk.Frame(page, bg=PAPER, highlightthickness=0, bd=0)
    body.grid(row=0, column=0, sticky="nsew", padx=28, pady=16)
    filler(app, body)
    return page


def _wrap(app, label: tk.Label) -> tk.Label:
    label.configure(wraplength=640)
    app._wraps.append(label)
    return label


def _picker(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    _heading(body, "Choose the board", app.version)
    for index, board in enumerate(BOARDS):
        Command(body, board.name, lambda board_id=board.id: app.choose_board(board_id)).grid(
            row=index * 2 + 1, column=0, sticky="w", pady=(16, 0)
        )
        _text(body, board.summary, 13, MUTED).grid(row=index * 2 + 2, column=0, sticky="w", pady=(6, 0))


def _wiring(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(6, weight=1)
    app.wire_title = tk.StringVar(value="Wire the sign")
    _heading(body, "", app.version, app.wire_title)
    table = tk.Frame(body, bg=SHEET, highlightthickness=0, bd=0)
    table.grid(row=1, column=0, sticky="ew")
    for column in range(3):
        table.grid_columnconfigure(column, weight=1)
    app.pin_cells = []
    for row_index in range(7):
        row_cells = []
        for column in range(3):
            label = _text(table, "", 12 if row_index == 0 else 13, MUTED if row_index == 0 else INK)
            label.grid(row=row_index, column=column, sticky="ew", padx=(0, 16), pady=5)
            row_cells.append(label)
        app.pin_cells.append(row_cells)
    app.wire_note = _wrap(
        app,
        _text(body, "Each display has a second header. That header is the same four pins. Use one header only.", 13),
    )
    app.wire_note.grid(row=2, column=0, sticky="ew", pady=(16, 0))
    app.wire_caution = _wrap(app, _text(body, "", 13, MUTED))
    app.wire_caution.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    actions = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    actions.grid(row=4, column=0, sticky="w", pady=(16, 0))
    Command(actions, "Next", app.show_upload).pack(side="left")
    Command(actions, "Change board", app.show_picker, kind="quiet").pack(side="left", padx=(8, 0))


def _upload(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(6, weight=1)
    app.upload_title = tk.StringVar(value="Upload")
    _heading(body, "", app.version, app.upload_title)
    app.upload_lead = tk.StringVar(value="Plug the board into the PC with a data USB cable.")
    _text(body, "", 13, variable=app.upload_lead).grid(row=1, column=0, sticky="w")
    actions = tk.Frame(body, bg=SHEET, highlightthickness=0, bd=0)
    actions.grid(row=2, column=0, sticky="w", pady=(16, 0))
    app.upload_button = Command(actions, "Upload", app.start_upload)
    app.upload_button.pack(side="left")
    Command(actions, "Skip", app.skip_programming, kind="quiet").pack(side="left", padx=(8, 0))
    _text(body, "Choose Skip if this board already has the program.", 12, MUTED).grid(
        row=3, column=0, sticky="w", pady=(12, 0)
    )
    status = _wrap(app, _text(body, "", 13, variable=app.upload_status))
    status.grid(row=4, column=0, sticky="ew", pady=(12, 0))
    app.continue_button = Command(body, "Continue", app.finish_setup)


def _wifi(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(2, weight=1)
    app.wifi_title = tk.StringVar(value="Wi-Fi and city")
    _heading(body, "", app.version, app.wifi_title)
    _text(body, "The board scans. Pick a network, then it updates on its own.", 13).grid(row=1, column=0, sticky="w")
    app.network_list = tk.Listbox(
        body,
        height=8,
        bg=FIELD,
        fg=INK,
        selectbackground=ACCENT,
        selectforeground="#f4f8fc",
        highlightthickness=0,
        bd=0,
        font=("Segoe UI", 12),
        activestyle="none",
    )
    app.network_list.grid(row=2, column=0, sticky="nsew", pady=(12, 8))
    Command(body, "Scan again", app.start_scan, kind="quiet").grid(row=3, column=0, sticky="w")
    form = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    form.grid(row=4, column=0, sticky="ew", pady=(16, 0))
    form.grid_columnconfigure(1, weight=1)
    _text(form, "Password", 13).grid(row=0, column=0, sticky="w", padx=(0, 16), pady=6)
    password_shell, app.password_entry = _entry(form, app.wifi_password)
    password_shell.grid(row=0, column=1, sticky="ew", pady=6)
    app.password_entry.configure(show="*")
    _text(form, "City", 13).grid(row=1, column=0, sticky="w", padx=(0, 16), pady=6)
    city_shell, _city_entry = _entry(form, app.address)
    city_shell.grid(row=1, column=1, sticky="ew", pady=6)
    _text(form, "Update every", 13).grid(row=2, column=0, sticky="w", padx=(0, 16), pady=6)
    gap = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    gap.grid(row=2, column=1, sticky="w", pady=6)
    app.wifi_interval_box = _minutes_entry(gap, app.interval_text)
    app.wifi_interval_box.pack(side="left", ipady=6, ipadx=8)
    app.wifi_interval_box.bind("<FocusOut>", lambda _event: app._commit_interval())
    app.wifi_interval_box.bind("<Return>", lambda _event: app._commit_interval())
    _text(gap, "minutes", 12, MUTED).pack(side="left", padx=(8, 0))
    app.save_button = Command(form, "Save on board", app.start_wifi_save)
    app.save_button.grid(row=3, column=1, sticky="w", pady=(12, 0))
    _text(body, "", 13, variable=app.wifi_status).grid(row=5, column=0, sticky="ew", pady=(12, 0))
    _text(body, "After it connects, unplug the board from the PC and power it from any USB port.", 12, MUTED).grid(
        row=6, column=0, sticky="w", pady=(8, 0)
    )
    Command(body, "Change board", app.show_picker, kind="quiet").grid(row=7, column=0, sticky="w", pady=(16, 0))


def _section(parent, title: str, row: int) -> None:
    _text(parent, title, 11, MUTED).grid(row=row, column=0, columnspan=3, sticky="w", pady=(12, 2))


def _prices(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(6, weight=1)
    _heading(body, "Prices", app.version)

    figures = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    figures.grid(row=1, column=0, sticky="w", pady=(4, 0))
    app._prices.append(_price(app, figures, "Regular", app.regular_show))
    app._prices.append(_price(app, figures, "Diesel", app.diesel_show))
    app._prices[1].pack(side="left", padx=(48, 0))
    source = _wrap(app, _text(body, "", 13, variable=app.source))
    source.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    app.note_label = _wrap(app, _text(body, "", 12, MUTED, app.status))
    app.note_label.grid(row=3, column=0, sticky="ew", pady=(2, 0))
    _text(body, "", 12, MUTED, app.unit_show).grid(row=4, column=0, sticky="w")

    form = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    form.grid(row=5, column=0, sticky="ew", pady=(8, 0))
    form.grid_columnconfigure(1, weight=1)
    _form(app, form)

    foot = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    foot.grid(row=7, column=0, sticky="ew", pady=(12, 0))
    app.wiring_link = _text(foot, "Wiring", 13, ACCENT)
    app.wiring_link.configure(cursor="hand2")
    app.wiring_link.pack(anchor="w")
    app.wiring_link.bind("<Button-1>", lambda _event: app.show_wiring())
    board_link = _text(foot, "Change board", 13, ACCENT)
    board_link.configure(cursor="hand2")
    board_link.pack(anchor="w", pady=(4, 0))
    board_link.bind("<Button-1>", lambda _event: app.show_picker())
    tray = _wrap(app, _text(foot, "", 12, MUTED, app.tray_text))
    tray.pack(anchor="w", pady=(6, 0))
    app.footer_label = tray


def _price(app, parent, caption: str, variable) -> tk.Frame:
    block = tk.Frame(parent, bg=SHEET, highlightthickness=0, bd=0)
    block.pack(side="left", anchor="n")
    _text(block, caption, 12, MUTED).pack(anchor="w")
    number = _text(block, "", 32, variable=variable)
    number.pack(anchor="w", pady=(2, 0))
    app._price_labels = getattr(app, "_price_labels", [])
    app._price_labels.append(number)
    return block


def _form(app, form: tk.Frame) -> None:
    _section(form, "Lookup", 0)
    _text(form, "City", 13).grid(row=1, column=0, sticky="w", padx=(0, 16), pady=4)
    city, app.address_entry = _entry(form, app.address)
    city.grid(row=1, column=1, sticky="ew", pady=4)
    app.address_entry.bind("<Return>", lambda _event: app.start_lookup(True))
    city_actions = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    city_actions.grid(row=1, column=2, sticky="w", padx=(12, 0), pady=4)
    app.lookup_button = Command(city_actions, "Look up", lambda: app.start_lookup(True))
    app.lookup_button.pack(side="left")
    Command(city_actions, "Clear", app.clear_search, kind="quiet").pack(side="left", padx=(8, 0))

    _text(form, "Update every", 13).grid(row=2, column=0, sticky="w", padx=(0, 16), pady=4)
    gap = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    gap.grid(row=2, column=1, sticky="w", pady=4)
    app.interval_box = _minutes_entry(gap, app.interval_text)
    app.interval_box.pack(side="left", ipady=6, ipadx=8)
    app.interval_box.bind("<FocusOut>", lambda _event: app._commit_interval())
    app.interval_box.bind("<Return>", lambda _event: app._commit_interval())
    _text(gap, "minutes", 12, MUTED).pack(side="left", padx=(8, 0))

    _section(form, "Manual prices", 3)
    _text(form, "Regular", 13).grid(row=4, column=0, sticky="w", padx=(0, 16), pady=4)
    prices = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    prices.grid(row=4, column=1, sticky="ew", pady=4)
    prices.grid_columnconfigure(0, weight=1)
    prices.grid_columnconfigure(2, weight=1)
    regular, _regular_entry = _entry(prices, app.typed_regular)
    regular.grid(row=0, column=0, sticky="ew")
    _text(prices, "Diesel", 13).grid(row=0, column=1, sticky="w", padx=(16, 8))
    diesel, _diesel_entry = _entry(prices, app.typed_diesel)
    diesel.grid(row=0, column=2, sticky="ew")
    Command(form, "Set prices", app.set_prices).grid(row=4, column=2, sticky="w", padx=(12, 0), pady=4)

    _section(form, "Sign", 5)
    _text(form, "Port", 13).grid(row=6, column=0, sticky="w", padx=(0, 16), pady=4)
    port, app.port_combo = _combo(form, app.port_label)
    port.grid(row=6, column=1, sticky="ew", pady=4)
    port_actions = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    port_actions.grid(row=6, column=2, sticky="w", padx=(12, 0), pady=4)
    Command(port_actions, "Refresh", app.refresh_ports, kind="quiet").pack(side="left")
    app.connect_button = Command(port_actions, "Connect", app.on_connect_clicked)
    app.connect_button.pack(side="left", padx=(8, 0))

    link = _wrap(app, _text(form, "", 13, variable=app.link_status))
    link.grid(row=7, column=1, columnspan=2, sticky="ew", pady=(2, 8))

    _text(form, "Brightness", 13).grid(row=8, column=0, sticky="w", padx=(0, 16), pady=4)
    bright = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    bright.grid(row=8, column=1, columnspan=2, sticky="ew", pady=4)
    scale = ttk.Scale(
        bright,
        from_=0,
        to=7,
        variable=app.brightness,
        command=app._on_brightness,
        style="Gas.Horizontal.TScale",
    )
    scale.pack(side="left", fill="x", expand=True, padx=(0, 12))
    scale.bind("<ButtonRelease-1>", lambda _event: app.send_brightness())
    _text(bright, "", 13, variable=app.brightness_show).pack(side="left")


def apply_scale(app, width: int) -> None:
    size = max(28, min(40, round(32 * max(0.95, min(1.15, width / 1100)))))
    for label in getattr(app, "_price_labels", []):
        label.configure(font=("Segoe UI", size))
    wrap = max(280, width - 120)
    for label in app._wraps:
        label.configure(wraplength=wrap)


def show(app, page: tk.Frame) -> None:
    pages = (app.board_page, app.wiring_page, app.upload_page, app.wifi_page, app.price_page)
    names = ("board", "wiring", "upload", "wifi", "prices")
    for other in pages:
        if other.winfo_manager():
            other.grid_remove()
    page.grid(row=0, column=0, sticky="nsew")
    app.page = names[pages.index(page)]
