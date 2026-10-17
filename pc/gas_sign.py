"""Desktop window that looks up fuel prices and sends them to the Pro Micro sign.

The USB port stays open. A Pro Micro restarts when the port is first opened,
so this waits for the sketch to print READY before the first prices go out.
Later updates write another line on that same port. If READY appears again,
the last prices are sent again.

Closing or minimizing the window hides it in the system tray. The lookup
timer keeps running there. Quit on the tray icon is what closes the port.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import tkinter as tk
from ctypes import windll
from pathlib import Path
from tkinter import ttk

try:
    import pystray
    import serial
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
    from serial.tools import list_ports
except ImportError:
    sys.stderr.write("Install dependencies with: py -3 -m pip install -r requirements.txt\n")
    raise

from prices import PriceError, Lookup, average, format_display, lookup_address
from uploader import UploadError, upload_sketch

APP_VERSION = "1.6"
TOP_RGB = (247, 244, 238)
BOTTOM_RGB = (232, 226, 216)
CARD_RGB = (255, 255, 255)
INK_RGB = (28, 25, 22)
MUTED_RGB = (122, 116, 108)
LINE_RGB = (226, 219, 208)
ACCENT_RGB = (140, 92, 68)
FIELD_RGB = (255, 255, 255)
DISABLED_RGB = (232, 226, 218)


def _hex(color: tuple[int, ...]) -> str:
    return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"


def _mix(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


PAGE = _hex(TOP_RGB)
CARD = _hex(CARD_RGB)
INK = _hex(INK_RGB)
MUTED = _hex(MUTED_RGB)
LINE = _hex(LINE_RGB)
ACCENT = _hex(ACCENT_RGB)
FIELD = _hex(FIELD_RGB)
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "gas_sign_config.json"
UNIT_LABELS = {
    "dollars_per_gallon": "dollars per gallon",
    "cents_per_litre": "cents per litre",
    "pence_per_litre": "pence per litre",
}
UPLOAD_RETRY = "Double-tap the reset button on the board and click Upload to sign again."
TRAY_LINE = (
    "Closing this window keeps updating prices in the system tray about every 30 minutes. "
    "Right-click the tray icon and choose Quit to stop."
)


def enable_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def make_tray_image() -> Image.Image:
    """A small price-sign icon. Drawn here so the app needs no image file."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 10, 58, 54), radius=8, fill=(16, 92, 52, 255))
    draw.rectangle((14, 20, 50, 30), fill=(236, 244, 232, 255))
    draw.rectangle((14, 36, 50, 46), fill=(236, 244, 232, 255))
    return image


def load_app_icon(size: int = 64) -> Image.Image:
    candidates = [Path(__file__).resolve().parent.parent / "assets" / "icon.png"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(getattr(sys, "_MEIPASS", "")) / "assets" / "icon.png")
    for path in candidates:
        if path.is_file():
            try:
                image = Image.open(path).convert("RGBA")
                return image.resize((size, size), Image.Resampling.LANCZOS)
            except Exception:
                break
    return make_tray_image()


def parse_price(text: str) -> float | None:
    cleaned = text.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    lower = cleaned.lower()
    for suffix in ("ppl", "/gal", "gal", "p"):
        if lower.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    return float(cleaned)


def price_unit(values: list[float], fallback: str) -> str:
    if not values:
        return fallback or "dollars_per_gallon"
    if all(value >= 20 for value in values):
        if fallback in {"pence_per_litre", "cents_per_litre"}:
            return fallback
        return "cents_per_litre"
    if all(value < 20 for value in values):
        return "dollars_per_gallon"
    raise PriceError("Those prices mix dollars and cents. Use one unit.")


def entry_text(price: float | None, unit: str) -> str:
    if price is None:
        return ""
    if unit in {"pence_per_litre", "cents_per_litre"}:
        return f"{price:.1f}"
    return f"{price:.3f}"


def first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


_FONT_FILES = {
    False: "segoeui.ttf",
    True: "segoeuib.ttf",
}
_FONT_CACHE: dict[tuple[int, bool], ImageFont.ImageFont] = {}
CARD_RADIUS = 28
BUTTON_RADIUS = 12
# Room around the white panel so the soft shadow is inside the widget.
SHADOW = (16, 8, 16, 22)
CARD_PAD = 16


def _ui_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    key = (size, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    filename = _FONT_FILES[bold]
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts" / filename
    if path.is_file():
        face = ImageFont.truetype(str(path), size)
    else:
        face = ImageFont.load_default()
    _FONT_CACHE[key] = face
    return face


def _vertical(width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    height = max(height, 1)
    width = max(width, 1)
    column = Image.new("RGB", (1, height))
    pixels = column.load()
    last = max(height - 1, 1)
    for y in range(height):
        pixels[0, y] = _mix(top, bottom, y / last)
    return column.resize((width, height), Image.Resampling.BILINEAR)


class Surfaces:
    """Smooth cards, buttons, and the page wash. Drawn big, then scaled down."""

    def __init__(self) -> None:
        self._wash: Image.Image | None = None
        self._wash_size = (0, 0)
        self._panels: dict[tuple[int, int], Image.Image] = {}
        self._buttons: dict[tuple, Image.Image] = {}

    def background(self, width: int, height: int) -> Image.Image:
        width = max(width, 1)
        height = max(height, 1)
        if self._wash is not None and self._wash_size == (width, height):
            return self._wash
        image = _vertical(width, height, TOP_RGB, BOTTOM_RGB)
        self._wash = image
        self._wash_size = (width, height)
        return image

    def color_at(self, y: int, height: int) -> str:
        last = max(height - 1, 1)
        return _hex(_mix(TOP_RGB, BOTTOM_RGB, max(0, min(last, y)) / last))

    def panel(self, width: int, height: int) -> Image.Image:
        key = (max(40, width // 4 * 4), max(40, height // 4 * 4))
        cached = self._panels.get(key)
        if cached is not None:
            if key == (width, height):
                return cached
            return cached.resize((width, height), Image.Resampling.LANCZOS)
        scale = 3
        canvas = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        left, top, right, bottom = (edge * scale for edge in SHADOW)
        radius = CARD_RADIUS * scale
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (left, top + 8 * scale, canvas.width - right, canvas.height - bottom + 8 * scale),
            radius=radius,
            fill=(72, 56, 40, 46),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=14 * scale))
        canvas = Image.alpha_composite(canvas, shadow)
        ImageDraw.Draw(canvas).rounded_rectangle(
            (left, top, canvas.width - right - 1, canvas.height - bottom - 1),
            radius=radius,
            fill=CARD_RGB + (255,),
            outline=LINE_RGB + (255,),
            width=scale,
        )
        image = canvas.resize(key, Image.Resampling.LANCZOS)
        self._panels[key] = image
        if key == (width, height):
            return image
        return image.resize((width, height), Image.Resampling.LANCZOS)

    def button(
        self,
        text: str,
        kind: str,
        hover: bool,
        disabled: bool,
        font_size: int,
        height: int,
        radius: int,
    ) -> Image.Image:
        face = _ui_font(font_size * 4, bold=(kind == "primary" and not disabled))
        text_width = int(face.getlength(text))
        width = max(112 * 4, text_width + 22 * 4 * 2)
        key = (text, kind, hover, disabled, font_size, height, radius, width)
        cached = self._buttons.get(key)
        if cached is not None:
            return cached
        scale_h = max(height, 32) * 4
        image = Image.new("RGBA", (width, scale_h), (0, 0, 0, 0))
        rect = (2, 2, width - 3, scale_h - 3)
        curve = max(4, radius * 4)
        if disabled:
            fill_top = fill_bottom = DISABLED_RGB
            ink = MUTED_RGB
        elif kind == "primary":
            lift = 0.2 if hover else 0.08
            fill_top = _mix(ACCENT_RGB, (255, 255, 255), lift + 0.08)
            fill_bottom = _mix(ACCENT_RGB, (255, 255, 255), lift)
            ink = (255, 255, 255)
        else:
            fill_top = fill_bottom = (248, 244, 238) if hover else CARD_RGB
            ink = INK_RGB
        fill = _vertical(width, scale_h, fill_top, fill_bottom).convert("RGBA")
        mask = Image.new("L", (width, scale_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(rect, radius=curve, fill=255)
        fill.putalpha(mask)
        image = Image.alpha_composite(image, fill)
        draw = ImageDraw.Draw(image)
        if kind != "primary" and not disabled:
            draw.rounded_rectangle(rect, radius=curve, outline=LINE_RGB + (255,), width=4)
        draw.text((width / 2, scale_h / 2), text, font=face, fill=ink + (255,), anchor="mm")
        result = image.resize((max(1, width // 4), max(1, height)), Image.Resampling.LANCZOS)
        self._buttons[key] = result
        return result


class Pill(tk.Label):
    """Smooth button. configure(state=) and configure(text=) still work."""

    def __init__(self, parent, text: str, command, surfaces: Surfaces, kind: str = "primary") -> None:
        super().__init__(parent, bd=0, highlightthickness=0, bg=CARD, padx=0, pady=0, cursor="hand2")
        self._text = text
        self._command = command
        self._surfaces = surfaces
        self._kind = kind
        self._state = "normal"
        self._hover = False
        self._font = ("Segoe UI", 13)
        self._radius = BUTTON_RADIUS
        self._height = 40
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
            self.configure(cursor="arrow" if self._state == "disabled" else "hand2")
            self._paint()

    config = configure

    def set_font(self, font, radius: int, height: int) -> None:
        self._font = font
        self._radius = radius
        self._height = height
        self._paint()

    def _paint(self) -> None:
        size = self._font[1] if len(self._font) > 1 else 13
        image = self._surfaces.button(
            self._text,
            self._kind,
            self._hover,
            self._state == "disabled",
            int(size),
            self._height,
            self._radius,
        )
        self._photo = ImageTk.PhotoImage(image)
        self.configure(image=self._photo)

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


class GroupCard(tk.Frame):
    def __init__(self, parent, surfaces: Surfaces, page: tk.Frame) -> None:
        super().__init__(parent, bg=PAGE, highlightthickness=0, bd=0)
        self.surfaces = surfaces
        self.page = page
        self.photo = tk.Label(self, bd=0, highlightthickness=0, bg=PAGE)
        self.photo.place(x=0, y=0, relwidth=1, relheight=1)
        left, top, right, bottom = SHADOW
        self.body = tk.Frame(self, bg=CARD, highlightthickness=0, bd=0)
        self.body.pack(
            fill="both",
            expand=True,
            padx=(left + CARD_PAD, right + CARD_PAD),
            pady=(top + CARD_PAD, bottom + CARD_PAD),
        )
        self._photo: ImageTk.PhotoImage | None = None
        self.photo.lower(self.body)

    def redraw(self, wash: Image.Image) -> None:
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 40 or height < 40:
            return
        x = self.winfo_rootx() - self.page.winfo_rootx()
        y = self.winfo_rooty() - self.page.winfo_rooty()
        overlay = self.surfaces.panel(width, height)
        crop = Image.new("RGB", (width, height), BOTTOM_RGB)
        left = max(0, x)
        top = max(0, y)
        right = min(wash.width, x + width)
        lower = min(wash.height, y + height)
        if right > left and lower > top:
            piece = wash.crop((left, top, right, lower))
            crop.paste(piece, (left - x, top - y))
        image = Image.alpha_composite(crop.convert("RGBA"), overlay).convert("RGB")
        self._photo = ImageTk.PhotoImage(image)
        self.photo.configure(image=self._photo)
        self.photo.lower(self.body)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Desktop Gas Sign {APP_VERSION}")
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1280, max(1140, screen_w - 64))
        height = min(1000, max(920, screen_h - 140))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 8)
        self.root.minsize(min(1140, width), min(920, height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.resizable(True, True)

        self.address = tk.StringVar()
        self.place = tk.StringVar()
        self.summary = tk.StringVar()
        self.note = tk.StringVar()
        self.typed_regular = tk.StringVar()
        self.typed_diesel = tk.StringVar()
        self.regular_show = tk.StringVar(value="----")
        self.diesel_show = tk.StringVar(value="----")
        self.unit_show = tk.StringVar()
        self.source = tk.StringVar()
        self.status = tk.StringVar()
        self.link_status = tk.StringVar()
        self.upload_status = tk.StringVar()
        self.port_label = tk.StringVar()
        self.brightness = tk.IntVar(value=4)
        self.brightness_show = tk.StringVar(value="4")
        self._brightness_lock = False
        self._last_brightness_sent: int | None = None
        self.refresh_minutes = tk.IntVar(value=30)

        self.unit = "dollars_per_gallon"
        self.regular_text = "----"
        self.diesel_text = "----"
        self.user_edited = False
        self._filling = False
        self.lookup_running = False
        self.lookup_generation = 0
        self.upload_running = False
        self.setup_done = False
        self.ser = None
        self.rx = ""
        self.saw_ready = False
        self.last_payload = ""
        self.port_map: dict[str, str] = {}
        self.refresh_job = None
        self.icon: pystray.Icon | None = None
        self.tray_ok = False
        self._quitting = False
        self._hiding = False
        self._told_tray = False
        self.page = ""

        self._build()
        self.typed_regular.trace_add("write", self._on_edit)
        self.typed_diesel.trace_add("write", self._on_edit)
        self.note.trace_add("write", self._sync_captions)
        self._load_config()
        self._sync_captions()
        self._start_tray()
        self.refresh_ports()
        self.refresh_preview()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.bind("<Configure>", self._on_resize)
        self.root.after(200, self.poll_serial)
        self.root.after(60_000, self.keepalive)
        self.schedule_refresh()
        if self.setup_done:
            self.show_prices()
            if self.address.get().strip() and not self.user_edited:
                self.start_lookup(manual=False)
            if self.port_map.get(self.port_label.get()):
                self.connect()
        else:
            self.show_wiring()

    def _build(self) -> None:
        self._wrap_labels: list[tuple[tk.Label, str]] = []
        self._roles: list[tuple[tk.Widget, str]] = []
        self._pills: list[Pill] = []
        self._entries: list[tk.Entry] = []
        self._cards: list[GroupCard] = []
        self._chrome: list[tuple[tk.Widget, tk.Frame]] = []
        self._gap = 8
        self._scale_bucket = None
        self._backdrop_size = (0, 0)
        self.surfaces = Surfaces()
        self._apply_style()
        self._window_icon = ImageTk.PhotoImage(load_app_icon(256))
        self.root.iconphoto(True, self._window_icon)
        self.root.configure(bg=PAGE)

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.shell = tk.Frame(self.root, bg=PAGE, highlightthickness=0, bd=0)
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.shell.grid_rowconfigure(0, weight=1)
        self.shell.grid_columnconfigure(0, weight=1)

        self.wiring_page = self._new_page()
        self.upload_page = self._new_page()
        self.price_page = self._new_page()
        self._build_wiring()
        self._build_upload()
        self._build_prices()
        self._apply_scale(1280)

    def _new_page(self) -> tk.Frame:
        page = tk.Frame(self.shell, bg=PAGE, highlightthickness=0, bd=0)
        wash = tk.Label(page, bd=0, highlightthickness=0, bg=PAGE)
        wash.place(x=0, y=0, relwidth=1, relheight=1)
        page.wash = wash
        page.grid_columnconfigure(0, weight=1)
        return page

    def _card(self, page: tk.Frame) -> GroupCard:
        card = GroupCard(page, self.surfaces, page)
        self._cards.append(card)
        return card

    def _apply_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "TCombobox",
            fieldbackground=FIELD,
            background=CARD,
            foreground=INK,
            arrowcolor=INK,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD)],
            foreground=[("readonly", INK)],
            selectbackground=[("readonly", FIELD)],
            selectforeground=[("readonly", INK)],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=FIELD,
            foreground=INK,
            arrowcolor=INK,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            padding=4,
        )
        style.configure("TScale", background=CARD, troughcolor="#E4DDD2")

    def _remember(self, widget: tk.Widget, role: str) -> tk.Widget:
        self._roles.append((widget, role))
        return widget

    def _text(self, parent, text: str, role: str, fg: str = INK, bg: str = CARD) -> tk.Label:
        label = tk.Label(parent, text=text, fg=fg, bg=bg, anchor="w", justify="left")
        return self._remember(label, role)

    def _var_text(self, parent, variable, role: str, fg: str = INK, bg: str = CARD) -> tk.Label:
        label = tk.Label(parent, textvariable=variable, fg=fg, bg=bg, anchor="w", justify="left")
        return self._remember(label, role)

    def _wrap(self, label: tk.Label, span: str) -> tk.Label:
        label.configure(wraplength=680 if span == "full" else 360)
        self._wrap_labels.append((label, span))
        return label

    def _entry(self, parent, variable) -> tk.Entry:
        entry = tk.Entry(
            parent,
            textvariable=variable,
            relief="flat",
            bd=0,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
        )
        self._entries.append(entry)
        return entry

    def _button(self, parent, text: str, command, kind: str = "primary") -> Pill:
        button = Pill(parent, text, command, self.surfaces, kind=kind)
        self._pills.append(button)
        return button

    def _track(self, widget: tk.Widget, page: tk.Frame) -> tk.Widget:
        self._chrome.append((widget, page))
        return widget

    def _page_title(self, page, text: str) -> None:
        bar = tk.Frame(page, bg=PAGE, highlightthickness=0, bd=0)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(18, 2))
        bar.grid_columnconfigure(0, weight=1)
        self._track(bar, page)
        copy = tk.Frame(bar, bg=PAGE, highlightthickness=0, bd=0)
        copy.grid(row=0, column=0, sticky="w")
        self._track(copy, page)
        eyebrow = self._text(copy, "Desktop gas sign", "caption", fg=MUTED, bg=PAGE)
        eyebrow.pack(anchor="w")
        self._track(eyebrow, page)
        title = self._text(copy, text, "title", bg=PAGE)
        title.pack(anchor="w", pady=(2, 0))
        self._track(title, page)
        version = self._text(bar, APP_VERSION, "caption", fg=MUTED, bg=PAGE)
        version.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        self._track(version, page)

    def _build_wiring(self) -> None:
        page = self.wiring_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._page_title(page, "Wire the sign")
        card = self._card(page)
        card.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 16))
        table = card.body
        for column in range(3):
            table.grid_columnconfigure(column, weight=1)
        rows = (
            ("Pro Micro", "Top display", "Bottom display"),
            ("VCC", "5V", "5V"),
            ("GND", "GND", "GND"),
            ("Pin 4", "CLK", ""),
            ("Pin 5", "DIO", ""),
            ("Pin 6", "", "CLK"),
            ("Pin 7", "", "DIO"),
        )
        for row_index, row in enumerate(rows):
            for column, cell in enumerate(row):
                fg = MUTED if row_index == 0 else INK
                role = "caption" if row_index == 0 else "body"
                label = self._text(table, cell, role, fg=fg)
                label.grid(row=row_index, column=column, sticky="ew", padx=(0, 24), pady=8)
        note = self._wrap(
            self._text(
                table,
                "Each display has a second header. That header is the same four pins. Use one header only.",
                "body",
            ),
            "full",
        )
        note.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        self._button(table, "Next", self.show_upload).grid(row=9, column=0, sticky="w", pady=(20, 0))

    def _build_upload(self) -> None:
        page = self.upload_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._page_title(page, "Upload")
        card = self._card(page)
        card.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 16))
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        self._text(body, "Plug the sign into the PC with the USB cable.", "body").grid(row=0, column=0, sticky="w")
        actions = tk.Frame(body, bg=CARD, highlightthickness=0, bd=0)
        actions.grid(row=1, column=0, sticky="w", pady=(18, 0))
        self.upload_button = self._button(actions, "Upload to sign", self.start_upload)
        self.upload_button.pack(side="left")
        self._button(actions, "Skip", self.finish_setup, kind="secondary").pack(side="left", padx=(12, 0))
        self._text(body, "Choose Skip if this sign already has the program.", "caption", fg=MUTED).grid(
            row=2, column=0, sticky="w", pady=(14, 0)
        )
        status = self._wrap(self._var_text(body, self.upload_status, "body"), "full")
        status.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.continue_button = self._button(body, "Continue", self.finish_setup)

    def _build_prices(self) -> None:
        page = self.price_page
        page.grid_columnconfigure(0, weight=1, uniform="desk")
        page.grid_columnconfigure(1, weight=1, uniform="desk")
        page.grid_rowconfigure(1, weight=1)
        page.grid_rowconfigure(2, weight=2)
        self._page_title(page, "Prices")

        current = self._card(page)
        current.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=16, pady=(4, 4))
        self._fill_current(current.body)

        lookup = self._card(page)
        lookup.grid(row=2, column=0, sticky="nsew", padx=(16, 6), pady=4)
        self._fill_lookup(lookup.body)

        manual = self._card(page)
        manual.grid(row=2, column=1, sticky="nsew", padx=(6, 16), pady=4)
        self._fill_manual(manual.body)

        sign = self._card(page)
        sign.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 4))
        self._fill_sign(sign.body)

        foot = tk.Frame(page, bg=PAGE, highlightthickness=0, bd=0)
        foot.grid(row=4, column=0, columnspan=2, sticky="ew", padx=36, pady=(2, 14))
        self._track(foot, page)
        self.wiring_link = self._text(foot, "Wiring", "body", fg=ACCENT, bg=PAGE)
        self.wiring_link.configure(cursor="hand2")
        self.wiring_link.pack(anchor="w")
        self.wiring_link.bind("<Button-1>", lambda _event: self.show_wiring())
        self._track(self.wiring_link, page)
        footer = self._wrap(self._text(foot, TRAY_LINE, "caption", fg=MUTED, bg=PAGE), "full")
        footer.pack(anchor="w", pady=(6, 0))
        self._track(footer, page)
        self.footer_label = footer

    def _fill_current(self, body: tk.Frame) -> None:
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        self._text(body, "Current price", "caption", fg=MUTED).grid(row=0, column=0, sticky="w")
        figures = tk.Frame(body, bg=CARD)
        figures.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        figures.grid_columnconfigure(0, weight=1)
        figures.grid_columnconfigure(1, weight=1)
        figures.grid_rowconfigure(0, weight=1)
        self._price_column(figures, "Regular", self.regular_show).grid(row=0, column=0, sticky="nsew")
        self._price_column(figures, "Diesel", self.diesel_show).grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        source = self._wrap(self._var_text(body, self.source, "body"), "full")
        source.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.note_label = self._wrap(self._var_text(body, self.status, "caption", fg=MUTED), "full")
        self.note_label.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._var_text(body, self.unit_show, "caption", fg=MUTED).grid(row=4, column=0, sticky="w", pady=(4, 0))

    def _price_column(self, parent, title: str, variable: tk.StringVar) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD)
        frame.grid_rowconfigure(1, weight=1)
        self._text(frame, title, "caption", fg=MUTED).grid(row=0, column=0, sticky="w")
        number = self._var_text(frame, variable, "price")
        number.grid(row=1, column=0, sticky="sw", pady=(4, 0))
        return frame

    def _fill_lookup(self, body: tk.Frame) -> None:
        body.grid_columnconfigure(0, weight=1)
        self._text(body, "Price lookup", "caption", fg=MUTED).grid(row=0, column=0, sticky="w")
        self._text(body, "City", "body").grid(row=1, column=0, sticky="w", pady=(8, 4))
        self.address_entry = self._entry(body, self.address)
        self.address_entry.grid(row=2, column=0, sticky="ew", ipady=7)
        self.address_entry.bind("<Return>", lambda _event: self.start_lookup(True))
        actions = tk.Frame(body, bg=CARD)
        actions.grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.lookup_button = self._button(actions, "Look up", lambda: self.start_lookup(True))
        self.lookup_button.pack(side="left")
        self._button(actions, "Clear", self.clear_search, kind="secondary").pack(side="left", padx=(10, 0))
        interval = tk.Frame(body, bg=CARD)
        interval.grid(row=4, column=0, sticky="w", pady=(12, 0))
        self._text(interval, "Update every", "body").pack(side="left")
        self.interval_box = ttk.Spinbox(
            interval,
            from_=15,
            to=240,
            textvariable=self.refresh_minutes,
            width=5,
            command=self._commit_interval,
        )
        self.interval_box.pack(side="left", padx=(10, 8))
        self.interval_box.bind("<FocusOut>", lambda _event: self._commit_interval())
        self.interval_box.bind("<Return>", lambda _event: self._commit_interval())
        self._roles.append((self.interval_box, "body"))
        self._text(interval, "minutes", "caption", fg=MUTED).pack(side="left")

    def _fill_manual(self, body: tk.Frame) -> None:
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self._text(body, "Manual pricing", "caption", fg=MUTED).grid(row=0, column=0, columnspan=2, sticky="w")
        self._text(body, "Regular", "body").grid(row=1, column=0, sticky="w", pady=(12, 4))
        self._text(body, "Diesel", "body").grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(12, 4))
        self._entry(body, self.typed_regular).grid(row=2, column=0, sticky="ew", ipady=7)
        self._entry(body, self.typed_diesel).grid(row=2, column=1, sticky="ew", padx=(12, 0), ipady=7)
        self._button(body, "Set prices", self.set_prices).grid(row=3, column=0, sticky="w", pady=(12, 0))

    def _fill_sign(self, body: tk.Frame) -> None:
        body.grid_columnconfigure(0, weight=1)
        self._text(body, "Sign", "caption", fg=MUTED).grid(row=0, column=0, sticky="w")
        row = tk.Frame(body, bg=CARD, highlightthickness=0, bd=0)
        row.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self._text(row, "Port", "body").pack(side="left")
        self.port_combo = ttk.Combobox(row, textvariable=self.port_label, state="readonly", width=36)
        self.port_combo.pack(side="left", padx=(12, 12))
        self._roles.append((self.port_combo, "body"))
        self._button(row, "Refresh", self.refresh_ports, kind="secondary").pack(side="left")
        self.connect_button = self._button(row, "Connect", self.on_connect_clicked)
        self.connect_button.pack(side="left", padx=(12, 0))
        link = self._wrap(self._var_text(row, self.link_status, "body"), "half")
        link.pack(side="left", padx=(16, 0))
        bright = tk.Frame(body, bg=CARD, highlightthickness=0, bd=0)
        bright.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._text(bright, "Brightness", "body").pack(side="left")
        scale = ttk.Scale(bright, from_=0, to=7, variable=self.brightness, command=self._on_brightness)
        scale.pack(side="left", fill="x", expand=True, padx=(12, 12))
        scale.bind("<ButtonRelease-1>", lambda _event: self.send_brightness())
        self._var_text(bright, self.brightness_show, "body").pack(side="left")

    def _metrics(self, width: int) -> dict[str, int]:
        scale = max(0.92, min(1.28, width / 1280))
        return {
            "caption": max(12, min(14, round(12 * scale))),
            "body": max(13, min(16, round(13 * scale))),
            "title": max(26, min(34, round(28 * scale))),
            "price": max(40, min(64, round(48 * scale))),
            "button": max(36, min(44, round(40 * scale))),
        }

    def _face(self, role: str, size: int):
        if role == "price":
            return ("Segoe UI Variable Display", size, "bold")
        if role == "title":
            return ("Segoe UI Variable Display", size)
        return ("Segoe UI", size)

    def _apply_scale(self, width: int) -> None:
        sizes = self._metrics(width)
        for widget, role in self._roles:
            try:
                widget.configure(font=self._face(role, sizes[role]))
            except tk.TclError:
                pass
        for entry in self._entries:
            entry.configure(font=("Segoe UI", sizes["body"]))
        style = ttk.Style()
        style.configure("TCombobox", font=("Segoe UI", sizes["body"]))
        style.configure("TSpinbox", font=("Segoe UI", sizes["body"]))
        for pill in self._pills:
            pill.set_font(("Segoe UI", sizes["body"]), BUTTON_RADIUS, sizes["button"])
        self._fit_wrap(width)

    def _fit_wrap(self, width: int) -> None:
        inset = SHADOW[0] + SHADOW[2] + CARD_PAD * 2 + 48
        half = max(220, int((width - 48) / 2) - inset)
        full = max(320, width - inset)
        for label, span in self._wrap_labels:
            label.configure(wraplength=full if span == "full" else half)

    def _paint_backdrop(self) -> None:
        self._backdrop_job = None
        page = {"wiring": self.wiring_page, "upload": self.upload_page, "prices": self.price_page}.get(self.page)
        if page is None:
            return
        width, height = page.winfo_width(), page.winfo_height()
        if width < 200 or height < 200:
            return
        wash = self.surfaces.background(width, height)
        page._wash_photo = ImageTk.PhotoImage(wash)
        page.wash.configure(image=page._wash_photo)
        page.wash.lower()
        for card in self._cards:
            if card.page is page:
                card.redraw(wash)
        self._sync_chrome(page, height)

    def _sync_chrome(self, page: tk.Frame, height: int) -> None:
        for widget, owner in self._chrome:
            if owner is not page:
                continue
            try:
                if not int(widget.winfo_ismapped()):
                    continue
                y = widget.winfo_rooty() - page.winfo_rooty() + widget.winfo_height() // 2
            except tk.TclError:
                continue
            widget.configure(bg=self.surfaces.color_at(y, height))

    def _show(self, page: tk.Frame) -> None:
        for other in (self.wiring_page, self.upload_page, self.price_page):
            if other.winfo_manager():
                other.grid_remove()
        page.grid(row=0, column=0, sticky="nsew")
        self.page = "wiring" if page is self.wiring_page else "upload" if page is self.upload_page else "prices"
        self.root.after_idle(self._paint_backdrop)

    def show_wiring(self) -> None:
        self._show(self.wiring_page)

    def show_upload(self) -> None:
        self._show(self.upload_page)
        self._set_continue_visible(self.setup_done)

    def _set_continue_visible(self, show: bool) -> None:
        if show:
            self.continue_button.grid(row=4, column=0, sticky="w", pady=(18, 0))
        else:
            self.continue_button.grid_remove()

    def show_prices(self) -> None:
        self._show(self.price_page)

    def finish_setup(self) -> None:
        self.setup_done = True
        self.save_config()
        self.show_prices()

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Update now", self._tray_update),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.icon = pystray.Icon("desktop_gas_sign", load_app_icon(64), "Desktop Gas Sign", menu)
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
        self.tray_ok = True

    def _tray_show(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.root.after(0, self.show_window)

    def _tray_update(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self.root.after(0, lambda: self.start_lookup(True))

    def _tray_quit(self, icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        icon.stop()
        self.icon = None
        self.root.after(0, self._finish_quit)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        if self.page == "prices":
            self.address_entry.focus_set()

    def hide_window(self) -> None:
        if self._quitting or self._hiding:
            return
        if not self.tray_ok:
            self._finish_quit()
            return
        self._hiding = True
        try:
            self.save_config()
            self.root.withdraw()
        finally:
            self._hiding = False
        if self._told_tray or self.icon is None:
            return
        self._told_tray = True
        try:
            self.icon.notify(
                "Still running. It looks up this city about every 30 minutes. Right-click the icon and choose Quit to exit.",
                "Desktop Gas Sign",
            )
        except Exception:
            pass

    def _on_unmap(self, event) -> None:
        if self._quitting or self._hiding or event.widget is not self.root:
            return
        try:
            iconic = self.root.state() == "iconic"
        except tk.TclError:
            return
        if iconic:
            self.root.after(0, self.hide_window)

    def _finish_quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.save_config()
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        port = self.ser
        self.ser = None
        self.saw_ready = False
        if port is not None:
            try:
                port.close()
            except serial.SerialException:
                pass
        icon = self.icon
        self.icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def _on_resize(self, event) -> None:
        if event.widget is not self.root or event.width < 200:
            return
        bucket = event.width // 24
        if bucket != self._scale_bucket:
            self._scale_bucket = bucket
            self._apply_scale(event.width)
        else:
            self._fit_wrap(event.width)
        size = (event.width, event.height)
        if size == self._backdrop_size:
            return
        self._backdrop_size = size
        job = getattr(self, "_backdrop_job", None)
        if job is not None:
            self.root.after_cancel(job)
        self._backdrop_job = self.root.after(40, self._paint_backdrop)

    def _on_edit(self, *_args) -> None:
        if self._filling:
            return
        self.user_edited = True
        self.refresh_preview()
        self._sync_captions()

    def _on_brightness(self, value: str) -> None:
        if self._brightness_lock:
            return
        try:
            level = int(round(float(value)))
        except ValueError:
            return
        level = max(0, min(7, level))
        self._brightness_lock = True
        try:
            if self.brightness.get() != level:
                self.brightness.set(level)
            self.brightness_show.set(str(level))
        finally:
            self._brightness_lock = False
        if self.ser is not None and level != self._last_brightness_sent:
            self._send_brightness_level(level)

    def _set_tray_title(self) -> None:
        if self.icon is None:
            return
        self.icon.title = f"Regular {self.regular_text}    Diesel {self.diesel_text}"

    def mark_connected(self) -> None:
        self.saw_ready = True
        self.link_status.set("Connected")

    def refresh_ports(self) -> None:
        previous = self.port_map.get(self.port_label.get(), "")
        ports = list(list_ports.comports())

        def rank(port) -> int:
            text = f"{port.description} {port.manufacturer or ''} {port.hwid}".lower()
            if "pro micro" in text or "sparkfun" in text or "1b4f" in text:
                return 0
            if "arduino" in text or "leonardo" in text or "2341" in text:
                return 1
            return 2

        ports.sort(key=rank)
        self.port_map = {}
        labels = []
        for port in ports:
            label = f"{port.device} — {port.description}"
            self.port_map[label] = port.device
            labels.append(label)
        self.port_combo["values"] = labels
        for label, device in self.port_map.items():
            if device == previous:
                self.port_label.set(label)
                return
        if labels and rank(ports[0]) < 2 and not previous:
            self.port_label.set(labels[0])

    def selected_port(self) -> str:
        label = self.port_label.get()
        if label in self.port_map:
            return self.port_map[label]
        return label.split(" — ")[0].strip()

    def start_lookup(self, manual: bool) -> None:
        if self.lookup_running:
            return
        address = self.address.get().strip()
        if not address:
            self.note.set("Type a city first.")
            return
        if not manual and self.user_edited:
            return
        self.lookup_generation += 1
        token = self.lookup_generation
        self.lookup_running = True
        self.lookup_button.configure(state="disabled")
        self.note.set("Looking up prices…")
        threading.Thread(target=self._lookup_thread, args=(address, token), daemon=True).start()

    def _lookup_thread(self, address: str, token: int) -> None:
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(20)
        try:
            result = lookup_address(address)
        except PriceError as exc:
            self.root.after(0, lambda message=str(exc), token=token: self._lookup_failed(token, message))
        except Exception as exc:
            self.root.after(
                0,
                lambda message=str(exc), token=token: self._lookup_failed(token, f"Lookup failed: {message}"),
            )
        else:
            self.root.after(0, lambda result=result, token=token: self._lookup_done(token, result))
        finally:
            socket.setdefaulttimeout(previous_timeout)

    def _lookup_failed(self, token: int, message: str) -> None:
        if token != self.lookup_generation:
            return
        self.lookup_running = False
        self.lookup_button.configure(state="normal")
        self.note.set(message)

    def clear_search(self) -> None:
        self.lookup_generation += 1
        self.lookup_running = False
        self.lookup_button.configure(state="normal")
        self._filling = True
        try:
            self.address.set("")
            self.place.set("")
            self.summary.set("")
            self.note.set("")
            self.typed_regular.set("")
            self.typed_diesel.set("")
            self.user_edited = False
        finally:
            self._filling = False
        self.refresh_preview()
        self.save_config()
        self._sync_captions()

    def _lookup_done(self, token: int, result: Lookup) -> None:
        if token != self.lookup_generation:
            return
        self.lookup_running = False
        self.lookup_button.configure(state="normal")
        self._apply_lookup(result)
        self.user_edited = False
        self._sync_captions()
        if not self.refresh_preview():
            return
        self.save_config()
        if self.ser:
            self.send_current()

    def _apply_lookup(self, result: Lookup) -> None:
        regulars = [station.regular for station in result.stations if station.regular is not None]
        diesels = [station.diesel for station in result.stations if station.diesel is not None]
        if regulars or diesels:
            regular = average(regulars)
            diesel = average(diesels)
        else:
            regular = result.area_regular
            diesel = result.area_diesel
        self._filling = True
        try:
            self.place.set(result.place_name)
            self.summary.set(result.summary)
            self.note.set(first_line(result.summary))
            self.unit = result.unit
            self.typed_regular.set(entry_text(regular, result.unit))
            self.typed_diesel.set(entry_text(diesel, result.unit))
        finally:
            self._filling = False

    def refresh_preview(self) -> bool:
        try:
            regular = parse_price(self.typed_regular.get())
            diesel = parse_price(self.typed_diesel.get())
        except ValueError:
            self.note.set("Type a number for regular and diesel.")
            return False
        values = [value for value in (regular, diesel) if value is not None]
        try:
            unit = price_unit(values, self.unit)
            self.regular_text = format_display(regular, unit) if regular is not None else "----"
            self.diesel_text = format_display(diesel, unit) if diesel is not None else "----"
        except (PriceError, ValueError) as exc:
            self.note.set(str(exc))
            return False
        self.unit = unit
        self.regular_show.set(self.regular_text)
        self.diesel_show.set(self.diesel_text)
        self.unit_show.set(UNIT_LABELS.get(unit, "") if values else "")
        self._set_tray_title()
        return True

    def set_prices(self) -> None:
        if not self.refresh_preview():
            return
        self.save_config()
        if self.ser is None:
            self.note.set("Connect the sign first.")
            return
        self.send_current()

    def start_upload(self) -> None:
        if self.upload_running:
            return
        if self.ser is not None:
            self.mark_disconnected()
        self.upload_running = True
        self.upload_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.upload_status.set("Uploading")
        port = self.selected_port()
        threading.Thread(target=self._upload_thread, args=(port,), daemon=True).start()

    def _upload_thread(self, port: str) -> None:
        try:
            upload_sketch(port or None)
        except UploadError as exc:
            message = str(exc)
            if "Plug in" not in message:
                message = UPLOAD_RETRY
            self.root.after(0, lambda message=message: self._upload_finished(message, ok=False))
        except Exception:
            self.root.after(0, lambda: self._upload_finished(UPLOAD_RETRY, ok=False))
        else:
            self.root.after(0, lambda: self._upload_finished("Upload finished", ok=True))

    def _upload_finished(self, message: str, ok: bool = False) -> None:
        self.upload_running = False
        self.upload_button.configure(state="normal")
        self.connect_button.configure(state="normal")
        self.upload_status.set(message)
        self.refresh_ports()
        if ok:
            self._set_continue_visible(True)

    def on_connect_clicked(self) -> None:
        if self.ser:
            self.mark_disconnected("Not connected")
            return
        self.connect()

    def connect(self) -> None:
        port = self.selected_port()
        if not port:
            self.link_status.set("Choose the sign's port.")
            return
        try:
            self.ser = serial.Serial(port, 115200, timeout=0, write_timeout=2)
        except serial.SerialException:
            self.ser = None
            self.link_status.set("Could not open the port. Unplug the sign, plug it back in, and click Connect.")
            return
        self.saw_ready = False
        self.rx = ""
        self.connect_button.configure(text="Disconnect")
        self.link_status.set("Connecting to the sign...")
        self.root.after(12_000, self._connect_fallback)
        self.save_config()

    def _connect_fallback(self) -> None:
        if self.ser and not self.saw_ready:
            self.send_current()

    def mark_disconnected(self, message: str = "") -> None:
        port = self.ser
        self.ser = None
        self.saw_ready = False
        if port is not None:
            try:
                port.close()
            except serial.SerialException:
                pass
        self.connect_button.configure(text="Connect")
        self.link_status.set(message)

    def poll_serial(self) -> None:
        if self._quitting:
            return
        if self.ser is not None:
            try:
                data = self.ser.read(512)
            except serial.SerialException:
                self.mark_disconnected("The sign unplugged.")
                data = b""
            if data:
                self.rx += data.decode("utf-8", "replace")
                while "\n" in self.rx:
                    line, self.rx = self.rx.split("\n", 1)
                    self._on_serial_line(line.strip())
        self.root.after(200, self.poll_serial)

    def _on_serial_line(self, line: str) -> None:
        if not line:
            return
        if line == "READY":
            self.mark_connected()
            self.send_current()
            return
        if line.startswith("OK"):
            self.mark_connected()

    def send_brightness(self) -> None:
        level = max(0, min(7, int(self.brightness.get())))
        self.brightness_show.set(str(level))
        self.save_config()
        if self.ser is None:
            return
        self._send_brightness_level(level)

    def _send_brightness_level(self, level: int) -> None:
        self._last_brightness_sent = level
        if self.last_payload:
            lines = []
            replaced = False
            for line in self.last_payload.splitlines():
                if line.startswith("B") and len(line) > 1 and line[1].isdigit():
                    lines.append(f"B{level}")
                    replaced = True
                elif line:
                    lines.append(line)
            if not replaced:
                lines.insert(0, f"B{level}")
            self.last_payload = "\n".join(lines) + "\n"
        if self.ser is None:
            return
        try:
            self.ser.write(f"B{level}\n".encode("ascii"))
        except serial.SerialException:
            self.mark_disconnected("Could not write to the sign. Reconnect the USB cable.")

    def send_current(self) -> None:
        if not self.refresh_preview():
            return
        payload = f"B{self.brightness.get()}\nR{self.regular_text}\nD{self.diesel_text}\n"
        self._last_brightness_sent = self.brightness.get()
        self._write(payload)

    def _write(self, payload: str) -> bool:
        if self.ser is None:
            self.note.set("Connect the sign first.")
            return False
        try:
            self.ser.write(payload.encode("ascii"))
        except serial.SerialException:
            self.mark_disconnected("Could not write to the sign. Reconnect the USB cable.")
            return False
        self.last_payload = payload
        return True

    def keepalive(self) -> None:
        if self._quitting:
            return
        if self.ser is not None and self.last_payload:
            self._write(self.last_payload)
        self.root.after(60_000, self.keepalive)

    def _sync_captions(self, *_args) -> None:
        if self.user_edited:
            self.source.set("Typed in")
        else:
            summary = first_line(self.summary.get())
            self.source.set(summary or self.place.get().strip())
        note = self.note.get().strip()
        self.status.set("" if not note or note == self.source.get() else note)

    def _commit_interval(self) -> None:
        if self._filling:
            return
        value = self.minutes()
        self._filling = True
        try:
            current = self.refresh_minutes.get()
        except (tk.TclError, ValueError):
            current = None
        try:
            if current != value:
                self.refresh_minutes.set(value)
        finally:
            self._filling = False
        self.save_config()
        self.schedule_refresh()

    def minutes(self) -> int:
        try:
            value = int(self.refresh_minutes.get())
        except (tk.TclError, ValueError):
            return 30
        return min(240, max(15, value))

    def schedule_refresh(self) -> None:
        if self._quitting:
            return
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(self.minutes() * 60_000, self.auto_refresh)

    def auto_refresh(self) -> None:
        self.refresh_job = None
        if self._quitting:
            return
        if self.address.get().strip() and not self.user_edited:
            self.start_lookup(manual=False)
        self.schedule_refresh()

    def save_config(self) -> None:
        payload = {
            "setup_done": self.setup_done,
            "address": self.address.get(),
            "port": self.selected_port(),
            "brightness": self.brightness.get(),
            "refresh_minutes": self.minutes(),
            "user_edited": self.user_edited,
            "unit": self.unit,
            "place": self.place.get(),
            "summary": self.summary.get(),
            "typed_regular": self.typed_regular.get(),
            "typed_diesel": self.typed_diesel.get(),
        }
        CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_config(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._filling = True
        try:
            self.setup_done = bool(payload.get("setup_done"))
            self.address.set(payload.get("address") or "")
            self.place.set(payload.get("place") or "")
            self.summary.set(payload.get("summary") or "")
            self.note.set(first_line(self.summary.get()))
            self.brightness.set(int(payload.get("brightness") or 4))
            self.brightness_show.set(str(self.brightness.get()))
            self.refresh_minutes.set(int(payload.get("refresh_minutes") or 30))
            self.refresh_minutes.set(self.minutes())
            self.user_edited = bool(payload.get("user_edited"))
            self.unit = payload.get("unit") or "dollars_per_gallon"
            self.typed_regular.set(payload.get("typed_regular") or "")
            self.typed_diesel.set(payload.get("typed_diesel") or "")
            saved_port = payload.get("port") or ""
            if saved_port:
                self.port_label.set(saved_port)
                self.port_map[saved_port] = saved_port
        finally:
            self._filling = False


def main() -> None:
    enable_dpi()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
