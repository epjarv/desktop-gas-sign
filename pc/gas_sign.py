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
    from PIL import Image, ImageDraw, ImageTk
    from serial.tools import list_ports
except ImportError:
    sys.stderr.write("Install dependencies with: py -3 -m pip install -r requirements.txt\n")
    raise

from prices import PriceError, Lookup, average, format_display, lookup_address
from uploader import UploadError, upload_sketch

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


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Desktop Gas Sign")
        self.root.minsize(640, 640)
        self.root.geometry("760x740")

        self.address = tk.StringVar()
        self.place = tk.StringVar()
        self.summary = tk.StringVar()
        self.note = tk.StringVar()
        self.typed_regular = tk.StringVar()
        self.typed_diesel = tk.StringVar()
        self.regular_show = tk.StringVar(value="----")
        self.diesel_show = tk.StringVar(value="----")
        self.unit_show = tk.StringVar()
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
        self._load_config()
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
        self._apply_style()
        self._window_icon = ImageTk.PhotoImage(load_app_icon(256))
        self.root.iconphoto(True, self._window_icon)

        self.shell = ttk.Frame(self.root, padding=(36, 32))
        self.shell.pack(fill="both", expand=True)

        self.wiring_page = ttk.Frame(self.shell)
        self.upload_page = ttk.Frame(self.shell)
        self.price_page = ttk.Frame(self.shell)
        self._build_wiring()
        self._build_upload()
        self._build_prices()

    def _apply_style(self) -> None:
        paper = "#f4f1ea"
        card = "#fffcf7"
        ink = "#1c2822"
        muted = "#5d6a63"
        green = "#1d6b43"
        style = ttk.Style()
        style.theme_use("clam")
        self.root.configure(bg=paper)
        self.root.option_add("*Font", "Segoe UI 12")
        style.configure(".", background=paper, foreground=ink, font=("Segoe UI", 12))
        style.configure("TFrame", background=paper)
        style.configure("Card.TFrame", background=card)
        style.configure("TLabel", background=paper, foreground=ink, font=("Segoe UI", 12))
        style.configure("Title.TLabel", background=paper, foreground=ink, font=("Segoe UI", 28))
        style.configure("Body.TLabel", background=paper, foreground=ink, font=("Segoe UI", 14))
        style.configure("Muted.TLabel", background=paper, foreground=muted, font=("Segoe UI", 12))
        style.configure("Wire.TLabel", background=card, foreground=ink, font=("Segoe UI", 15))
        style.configure("WireHead.TLabel", background=card, foreground=ink, font=("Segoe UI", 13, "bold"))
        style.configure("Big.TLabel", background=paper, foreground=ink, font=("Segoe UI", 42, "bold"))
        style.configure("TEntry", fieldbackground=card, padding=8)
        style.configure("TCombobox", fieldbackground=card, padding=6)
        style.configure("TScale", background=paper)
        style.configure(
            "TButton",
            font=("Segoe UI", 12),
            padding=(16, 10),
            background=card,
            foreground=ink,
            borderwidth=1,
            focusthickness=0,
        )
        style.map("TButton", background=[("pressed", "#e6e0d4"), ("active", "#ffffff")])
        style.configure(
            "Accent.TButton",
            font=("Segoe UI", 12),
            padding=(18, 10),
            background=green,
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            focuscolor=green,
        )
        style.map(
            "Accent.TButton",
            background=[("pressed", "#154e32"), ("active", "#175636"), ("disabled", "#b7c9be")],
            foreground=[("disabled", "#f7f6f3")],
        )

    def _build_wiring(self) -> None:
        page = self.wiring_page
        ttk.Label(page, text="Wire the sign", style="Title.TLabel").pack(anchor="w")

        table = ttk.Frame(page, style="Card.TFrame", padding=(20, 16))
        table.pack(anchor="w", pady=(28, 0))
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
            for column, text in enumerate(row):
                kind = "WireHead.TLabel" if row_index == 0 else "Wire.TLabel"
                ttk.Label(table, text=text, style=kind, width=16).grid(
                    row=row_index, column=column, sticky="w", padx=(0, 28), pady=6
                )

        ttk.Label(
            page,
            text="Each display has a second header. That header is the same four pins. Use one header only.",
            style="Body.TLabel",
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(28, 28))
        ttk.Button(page, text="Next", style="Accent.TButton", command=self.show_upload).pack(anchor="w")

    def _build_upload(self) -> None:
        page = self.upload_page
        ttk.Label(page, text="Upload", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            page,
            text="Plug the sign into the PC with the USB cable.",
            style="Body.TLabel",
        ).pack(anchor="w", pady=(24, 28))
        actions = ttk.Frame(page)
        actions.pack(anchor="w")
        self.upload_button = ttk.Button(
            actions, text="Upload to sign", style="Accent.TButton", command=self.start_upload
        )
        self.upload_button.pack(side="left")
        ttk.Button(actions, text="Skip", command=self.finish_setup).pack(side="left", padx=(12, 0))
        ttk.Label(
            page,
            text="Choose Skip if this sign already has the program.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(14, 0))
        ttk.Label(page, textvariable=self.upload_status, style="Body.TLabel", wraplength=640).pack(
            anchor="w", pady=(20, 20)
        )
        self.continue_button = ttk.Button(page, text="Continue", style="Accent.TButton", command=self.finish_setup)

    def _build_prices(self) -> None:
        page = self.price_page
        footer = ttk.Label(page, text=TRAY_LINE, style="Muted.TLabel", wraplength=680, justify="left")
        footer.pack(side="bottom", anchor="w", pady=(24, 0))
        self.footer_label = footer

        ttk.Label(page, text="Prices", style="Title.TLabel").pack(anchor="w")

        city = ttk.Frame(page)
        city.pack(fill="x", pady=(28, 0))
        ttk.Label(city, text="City", style="Body.TLabel").pack(side="left")
        self.address_entry = ttk.Entry(city, textvariable=self.address, font=("Segoe UI", 14))
        self.address_entry.pack(side="left", fill="x", expand=True, padx=(12, 12))
        self.address_entry.bind("<Return>", lambda _event: self.start_lookup(True))
        self.lookup_button = ttk.Button(
            city, text="Look up", style="Accent.TButton", command=lambda: self.start_lookup(True)
        )
        self.lookup_button.pack(side="left")
        ttk.Button(city, text="Clear", command=self.clear_search).pack(side="left", padx=(12, 0))

        boxes = ttk.Frame(page)
        boxes.pack(fill="x", pady=(28, 0))
        ttk.Label(boxes, text="Regular", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(boxes, text="Diesel", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(24, 0))
        ttk.Entry(boxes, textvariable=self.typed_regular, width=12, font=("Segoe UI", 16)).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(boxes, textvariable=self.typed_diesel, width=12, font=("Segoe UI", 16)).grid(
            row=1, column=1, sticky="w", padx=(24, 0), pady=(8, 0)
        )
        ttk.Button(boxes, text="Set prices", style="Accent.TButton", command=self.set_prices).grid(
            row=1, column=2, sticky="sw", padx=(20, 0)
        )

        figures = ttk.Frame(page)
        figures.pack(fill="x", pady=(32, 0))
        self._price_column(figures, "Regular", self.regular_show).pack(side="left", expand=True, fill="x")
        self._price_column(figures, "Diesel", self.diesel_show).pack(side="left", expand=True, fill="x")

        self.note_label = ttk.Label(page, textvariable=self.note, style="Body.TLabel", wraplength=680, justify="left")
        self.note_label.pack(anchor="w", pady=(12, 0))
        ttk.Label(page, textvariable=self.unit_show, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        sign = ttk.Frame(page)
        sign.pack(fill="x", pady=(28, 0))
        ttk.Label(sign, text="Port", style="Body.TLabel").pack(side="left")
        self.port_combo = ttk.Combobox(sign, textvariable=self.port_label, width=28, state="readonly")
        self.port_combo.pack(side="left", padx=(12, 12))
        ttk.Button(sign, text="Refresh", command=self.refresh_ports).pack(side="left")
        self.connect_button = ttk.Button(sign, text="Connect", style="Accent.TButton", command=self.on_connect_clicked)
        self.connect_button.pack(side="left", padx=(12, 0))
        ttk.Label(sign, textvariable=self.link_status, style="Body.TLabel").pack(side="left", padx=(16, 0))

        bright = ttk.Frame(page)
        bright.pack(fill="x", pady=(22, 0))
        ttk.Label(bright, text="Brightness", style="Body.TLabel").pack(side="left")
        scale = ttk.Scale(bright, from_=0, to=7, variable=self.brightness, command=self._on_brightness)
        scale.pack(side="left", fill="x", expand=True, padx=(12, 12))
        scale.bind("<ButtonRelease-1>", lambda _event: self.send_brightness())
        ttk.Label(bright, textvariable=self.brightness_show, style="Body.TLabel", width=2).pack(side="left")

        self.wiring_link = tk.Label(
            page,
            text="Wiring",
            fg="#1d6b43",
            bg="#f4f1ea",
            cursor="hand2",
            font=("Segoe UI", 11, "underline"),
        )
        self.wiring_link.pack(anchor="w", pady=(22, 0))
        self.wiring_link.bind("<Button-1>", lambda _event: self.show_wiring())

    def _price_column(self, parent, title: str, variable: tk.StringVar) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=title, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=variable, style="Big.TLabel").pack(anchor="w", pady=(4, 0))
        return frame

    def _show(self, page: ttk.Frame) -> None:
        for other in (self.wiring_page, self.upload_page, self.price_page):
            other.pack_forget()
        page.pack(fill="both", expand=True)
        self.page = "wiring" if page is self.wiring_page else "upload" if page is self.upload_page else "prices"

    def show_wiring(self) -> None:
        self._show(self.wiring_page)

    def show_upload(self) -> None:
        self._show(self.upload_page)
        if self.setup_done:
            self.continue_button.pack(anchor="w")
        else:
            self.continue_button.pack_forget()

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
        if event.widget is not self.root:
            return
        width = max(420, event.width - 56)
        self.note_label.configure(wraplength=width)
        self.footer_label.configure(wraplength=width)

    def _on_edit(self, *_args) -> None:
        if self._filling:
            return
        self.user_edited = True
        self.refresh_preview()

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

    def _lookup_done(self, token: int, result: Lookup) -> None:
        if token != self.lookup_generation:
            return
        self.lookup_running = False
        self.lookup_button.configure(state="normal")
        self._apply_lookup(result)
        self.user_edited = False
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
            self.continue_button.pack(anchor="w")

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
