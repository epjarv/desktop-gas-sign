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
from view import apply_scale, build as build_view, show as show_page

APP_VERSION = "1.8"
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
        self.root.title(f"Desktop Gas Sign {APP_VERSION}")
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1100, max(960, screen_w - 80))
        height = min(760, max(700, screen_h - 160))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 6)
        self.root.minsize(960, 700)
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
        self.interval_text = tk.StringVar(value="30")

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
        self.version = APP_VERSION
        self.tray_line = TRAY_LINE
        self._icon_image = load_app_icon(256)
        build_view(self)

    def _show(self, page: tk.Frame) -> None:
        show_page(self, page)

    def show_wiring(self) -> None:
        self._show(self.wiring_page)

    def show_upload(self) -> None:
        self._show(self.upload_page)
        self._set_continue_visible(self.setup_done)

    def _set_continue_visible(self, show: bool) -> None:
        if show:
            self.continue_button.grid(row=5, column=0, sticky="w", pady=(16, 0))
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
        if event.widget is not self.root or event.width < 400:
            return
        apply_scale(self, event.width)

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
        raw = self.interval_text.get().strip()
        try:
            value = int(raw)
        except ValueError:
            self._filling = True
            try:
                self.interval_text.set(str(self.refresh_minutes.get()))
            finally:
                self._filling = False
            return
        value = min(240, max(15, value))
        previous = self.refresh_minutes.get()
        self._filling = True
        try:
            self.refresh_minutes.set(value)
            self.interval_text.set(str(value))
        finally:
            self._filling = False
        self.save_config()
        if value != previous:
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
            self.interval_text.set(str(self.refresh_minutes.get()))
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
