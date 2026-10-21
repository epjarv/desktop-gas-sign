"""Windows setup for the ESP8266 sign. Later updates happen on the board."""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.stderr.write("Install dependencies with: py -3 -m pip install -r requirements.txt\n")
    raise

from esp_upload import UploadError, flash_board
import esp_view

APP_VERSION = "1.12"


def clamp_minutes(value, fallback: int = 30) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return min(240, max(15, number))


def port_rank(port) -> int:
    text = f"{port.description} {port.manufacturer or ''} {port.hwid}".lower()
    if "1a86" in text or "ch340" in text or "cp210" in text or "10c4" in text or "silicon labs" in text:
        return 0
    if "usb" in text or "uart" in text or "serial" in text:
        return 1
    return 2


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.version = APP_VERSION
        self.root.title(f"Desktop Gas Sign ESP8266 NodeMCU {APP_VERSION}")
        self.root.minsize(860, 700)
        self.root.geometry("980x760")
        self.password = tk.StringVar()
        self.city = tk.StringVar()
        self.interval_text = tk.StringVar(value="30")
        self.upload_status = tk.StringVar()
        self.setup_status = tk.StringVar(value="Upload the program, or choose Skip if it is already there.")
        self.ser = None
        self.rx = ""
        self.upload_running = False
        self.save_running = False
        self.board_ready = False
        self.price_note = ""
        self._quitting = False
        esp_view.build(self)
        esp_view.show(self, self.wiring_page)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self.poll_serial)

    def show_upload(self) -> None:
        esp_view.show(self, self.upload_page)

    def show_setup(self) -> None:
        esp_view.show(self, self.setup_page)
        self.setup_status.set("Click Scan again if the list is empty.")
        self.open_serial()
        self.root.after(1500, self.start_scan)

    def close(self) -> None:
        self._quitting = True
        self.close_serial()
        self.root.destroy()

    def chosen_port(self) -> str:
        ports = sorted(list_ports.comports(), key=port_rank)
        if not ports:
            return ""
        if port_rank(ports[0]) < 2:
            return ports[0].device
        return ports[0].device

    def close_serial(self) -> None:
        port = self.ser
        self.ser = None
        if port is not None:
            try:
                port.close()
            except serial.SerialException:
                pass

    def open_serial(self) -> bool:
        self.close_serial()
        port = self.chosen_port()
        if not port:
            self.setup_status.set("Plug in the NodeMCU.")
            return False
        try:
            self.ser = serial.Serial(port, 115200, timeout=0)
        except serial.SerialException:
            self.ser = None
            self.setup_status.set("Could not open the port. Unplug the board, plug it back in, and try again.")
            return False
        self.rx = ""
        time.sleep(0.4)
        return True

    def start_upload(self) -> None:
        if self.upload_running:
            return
        port = self.chosen_port()
        if not port:
            self.upload_status.set("Plug in the NodeMCU, then click Upload to NodeMCU.")
            return
        self.upload_running = True
        self.upload_button.configure(state="disabled")
        self.upload_status.set("Uploading")
        self.close_serial()
        threading.Thread(target=self._upload_thread, args=(port,), daemon=True).start()

    def _upload_thread(self, port: str) -> None:
        try:
            flash_board(port)
        except UploadError as exc:
            message = str(exc)
            self.root.after(0, lambda message=message: self._upload_done(message, False))
        except Exception:
            self.root.after(0, lambda: self._upload_done("The upload did not finish. Try again.", False))
        else:
            self.root.after(0, lambda: self._upload_done("Upload finished", True))

    def _upload_done(self, message: str, ok: bool) -> None:
        self.upload_running = False
        self.upload_button.configure(state="normal")
        self.upload_status.set(message)
        if ok:
            self.show_setup()

    def start_scan(self) -> None:
        if self.ser is None and not self.open_serial():
            return
        self.setup_status.set("Scanning")
        self.network_list.delete(0, "end")
        try:
            self.ser.write(b"SCAN\n")
        except serial.SerialException:
            self.setup_status.set("The board unplugged.")
            self.close_serial()

    def selected_ssid(self) -> str:
        picked = self.network_list.curselection()
        if not picked:
            return ""
        text = self.network_list.get(picked[0])
        return text.rsplit("  ", 1)[0]

    def start_save(self) -> None:
        if self.save_running:
            return
        ssid = self.selected_ssid()
        if not ssid:
            self.setup_status.set("Select a network first.")
            return
        city = self.city.get().strip()
        if not city:
            self.setup_status.set("Type a city.")
            return
        if self.ser is None and not self.open_serial():
            return
        minutes = clamp_minutes(self.interval_text.get(), 30)
        self.interval_text.set(str(minutes))
        self.save_running = True
        self.save_button.configure(state="disabled")
        self.setup_status.set("Saving on the board")
        password = self.password.get()
        threading.Thread(
            target=self._save_thread,
            args=(ssid, password, city, minutes),
            daemon=True,
        ).start()

    def _save_thread(self, ssid: str, password: str, city: str, minutes: int) -> None:
        try:
            self._write_line(f"WIFI\t{ssid}\t{password}")
            self._write_line(f"CITY\t{city}")
            self._write_line(f"INTERVAL\t{minutes}")
            self._write_line("SAVE")
        except serial.SerialException:
            self.root.after(0, lambda: self._save_done("The board unplugged."))
            return
        deadline = time.time() + 40
        error = "The board did not answer. Try Save on board again."
        while time.time() < deadline:
            line = self._wait_line(deadline)
            if line is None:
                break
            if line.startswith("OK CONNECTED"):
                self.root.after(0, lambda: self._save_done("Configured. The board will retry until a price arrives.", True))
                return
            if line.startswith("ERR PRICE"):
                self.price_note = line[9:].strip()
                continue
            if line.startswith("ERR"):
                error = line[4:].strip() or "The board reported an error."
                break
        self.root.after(0, lambda error=error: self._save_done(error, False))

    def _save_done(self, message: str, ready: bool = False) -> None:
        self.save_running = False
        self.save_button.configure(state="normal")
        if ready:
            self.board_ready = True
            if self.price_note:
                message = "Configured. It will retry. " + self.price_note
        self.setup_status.set(message)

    def _write_line(self, text: str) -> None:
        if self.ser is None:
            raise serial.SerialException("closed")
        self.ser.write((text + "\n").encode("utf-8"))

    def _wait_line(self, deadline: float) -> str | None:
        while time.time() < deadline:
            if self.ser is None:
                return None
            try:
                data = self.ser.read(256)
            except serial.SerialException:
                return None
            if data:
                self.rx += data.decode("utf-8", "replace")
            if "\n" in self.rx:
                line, self.rx = self.rx.split("\n", 1)
                return line.strip()
            time.sleep(0.05)
        return None

    def poll_serial(self) -> None:
        if self._quitting:
            return
        if self.ser is not None and not self.save_running:
            try:
                data = self.ser.read(512)
            except serial.SerialException:
                self.close_serial()
                data = b""
            if data:
                self.rx += data.decode("utf-8", "replace")
                while "\n" in self.rx:
                    line, self.rx = self.rx.split("\n", 1)
                    self._on_line(line.strip())
        self.root.after(200, self.poll_serial)

    def _on_line(self, line: str) -> None:
        if not line or "\t" not in line or line.startswith("OK") or line.startswith("ERR"):
            if line == "SCAN":
                self.setup_status.set("Scanning")
            elif line == "SCAN_DONE":
                self.setup_status.set("Select a network.")
            elif line.startswith("PRICE") and self.board_ready:
                self.setup_status.set("Updated. " + line[6:].strip())
            elif line.startswith("ERR PRICE"):
                reason = line[9:].strip()
                if self.board_ready:
                    self.setup_status.set("Configured. It will retry. " + reason)
                else:
                    self.price_note = reason
            elif line.startswith("ERR"):
                self.setup_status.set(line[4:].strip())
            return
        name, _, rssi = line.partition("\t")
        if name and name != "SCAN":
            self.network_list.insert("end", f"{name}  {rssi}")


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
