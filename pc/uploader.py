"""Upload the prebuilt Pro Micro sketch with the Caterina bootloader.

The hex and avrdude are stored next to this file, and inside the one-file
app. Nothing here asks the user to install the Arduino IDE.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

RETRY = "Double-tap the reset button on the board and click Upload again."
PLUG_IN = "Plug in the sign, then click Upload to sign."
MISSING = "The sign program is not included in this copy of the app."


class UploadError(Exception):
    """A failure the window can show in one sentence."""


def firmware_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "firmware"
    return Path(__file__).resolve().parent / "firmware"


def _text(port) -> str:
    return f"{port.description} {port.manufacturer or ''} {port.hwid}".lower()


def _rank(port) -> int:
    text = _text(port)
    if "pro micro" in text or "sparkfun" in text or "1b4f" in text or "caterina" in text:
        return 0
    if "arduino" in text or "leonardo" in text or "2341" in text:
        return 1
    return 2


def _is_bootloader(port) -> bool:
    text = _text(port)
    if "caterina" in text or "bootloader" in text:
        return True
    # Bootloader PIDs: SparkFun 5V 9205, SparkFun 3.3V 9203, Leonardo 0036.
    compact = text.replace(" ", "")
    return any(token in compact for token in (":9205", ":9203", ":0036"))


def _choose_port(preferred: str | None):
    ports = list(list_ports.comports())
    if preferred:
        for port in ports:
            if port.device.upper() == preferred.upper():
                return port
    ranked = sorted(ports, key=_rank)
    if ranked and _rank(ranked[0]) < 2:
        return ranked[0]
    return None


def _touch_1200(port: str) -> None:
    try:
        link = serial.Serial(port=port, baudrate=1200, timeout=1)
    except serial.SerialException as exc:
        raise UploadError(RETRY) from exc
    try:
        link.dtr = False
        link.rts = False
        time.sleep(0.3)
    finally:
        link.close()


def _wait_for_bootloader(sketch_port: str) -> str:
    before = {port.device for port in list_ports.comports()}
    missing = False
    deadline = time.time() + 10
    while time.time() < deadline:
        time.sleep(0.25)
        current = list(list_ports.comports())
        devices = {port.device: port for port in current}
        if sketch_port not in devices:
            missing = True
        for port in current:
            if _is_bootloader(port):
                return port.device
            if port.device not in before:
                return port.device
        if missing and sketch_port in devices:
            return sketch_port
    raise UploadError(RETRY)


def _avrdude(boot_port: str, hex_path: Path, tool: Path, config: Path) -> None:
    command = [
        str(tool),
        "-C",
        str(config),
        "-p",
        "atmega32u4",
        "-c",
        "avr109",
        "-P",
        boot_port,
        "-b",
        "57600",
        "-D",
        "-U",
        f"flash:w:{hex_path}:i",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UploadError(RETRY) from exc
    if result.returncode != 0:
        raise UploadError(RETRY)


def upload_sketch(preferred_port: str | None = None) -> None:
    folder = firmware_dir()
    hex_path = folder / "gas_sign.hex"
    tool = folder / "avrdude.exe"
    config = folder / "avrdude.conf"
    if not hex_path.is_file() or not tool.is_file() or not config.is_file():
        raise UploadError(MISSING)

    chosen = _choose_port(preferred_port)
    if chosen is None:
        raise UploadError(PLUG_IN)

    if _is_bootloader(chosen):
        boot_port = chosen.device
    else:
        _touch_1200(chosen.device)
        boot_port = _wait_for_bootloader(chosen.device)
    _avrdude(boot_port, hex_path, tool, config)
