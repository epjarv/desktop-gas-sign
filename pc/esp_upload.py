"""Flash the NodeMCU with the bundled ESP8266 sketch. No Arduino IDE."""

from __future__ import annotations

import sys
from pathlib import Path


class UploadError(Exception):
    """A failure the window can show in one sentence."""


PLUG_IN = "Plug in the board, then click Upload."
RETRY = "Hold the FLASH button if the board has one, then click Upload again."
MISSING = "The ESP8266 program is not included in this copy of the app."


def firmware_path(name: str = "gas_sign_nodemcu.bin") -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "firmware" / name
    return Path(__file__).resolve().parent / "firmware" / name


def flash_board(port: str, name: str = "gas_sign_nodemcu.bin") -> None:
    binary = firmware_path(name)
    if not binary.is_file():
        raise UploadError(MISSING)
    if not port:
        raise UploadError(PLUG_IN)
    try:
        import esptool
    except ImportError as exc:
        raise UploadError("The flasher is missing from this copy of the app.") from exc

    argv = [
        "--port",
        port,
        "--baud",
        "115200",
        "--before",
        "default_reset",
        "--after",
        "hard_reset",
        "write_flash",
        "--flash_mode",
        "dio",
        "--flash_freq",
        "40m",
        "--flash_size",
        "4MB",
        "0x0",
        str(binary),
    ]
    try:
        esptool.main(argv)
    except SystemExit as exc:
        code = exc.code
        if code not in (0, None):
            raise UploadError(RETRY) from exc
    except Exception as exc:
        raise UploadError(RETRY) from exc
