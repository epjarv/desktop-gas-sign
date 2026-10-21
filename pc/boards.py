"""Board profiles for the one Desktop Gas Sign window.

A new board is another entry here. The window reads the pin labels, the
firmware file, and whether the PC or the board fetches prices.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    summary: str
    kind: str
    firmware: str
    pins: tuple[tuple[str, str, str], ...]
    caution: str = ""


# Freenove silkscreen names come from their published diagram
# https://github.com/Freenove/Freenove_ESP8266_Board/blob/master/ESP8266_Pinout.jpg
# and the FNK0076 preface, which lists IO5, IO4, IO14, and IO12.
# Their tutorial flashes it as NodeMCU 1.0 (ESP-12E), 4 MB, DIO.
# Those GPIO numbers match the NodeMCU sketch, so both boards use one .bin.

BOARDS: tuple[Board, ...] = (
    Board(
        id="pro_micro",
        name="Arduino Pro Micro",
        summary="The PC looks up prices and sends them over USB. Leave this program running.",
        kind="arduino",
        firmware="gas_sign.hex",
        pins=(
            ("Pro Micro", "Top display", "Bottom display"),
            ("VCC", "5V", "5V"),
            ("GND", "GND", "GND"),
            ("Pin 4", "CLK", ""),
            ("Pin 5", "DIO", ""),
            ("Pin 6", "", "CLK"),
            ("Pin 7", "", "DIO"),
        ),
    ),
    Board(
        id="nodemcu",
        name="Lolin NodeMCU v3",
        summary="After Wi-Fi and a city are saved, the board updates by itself.",
        kind="esp",
        firmware="gas_sign_nodemcu.bin",
        pins=(
            ("NodeMCU", "Top display", "Bottom display"),
            ("VIN", "5V", "5V"),
            ("GND", "GND", "GND"),
            ("D1  GPIO5", "CLK", ""),
            ("D2  GPIO4", "DIO", ""),
            ("D5  GPIO14", "", "CLK"),
            ("D6  GPIO12", "", "DIO"),
        ),
        caution="Leave GPIO0, GPIO2, and GPIO15 alone. They decide how the board starts.",
    ),
    Board(
        id="freenove",
        name="Freenove ESP8266",
        summary="The small board. Same program as the NodeMCU. The printed pin names differ.",
        kind="esp",
        firmware="gas_sign_nodemcu.bin",
        pins=(
            ("Freenove", "Top display", "Bottom display"),
            ("5V", "5V", "5V"),
            ("GND", "GND", "GND"),
            ("5  GPIO5", "CLK", ""),
            ("4  GPIO4", "DIO", ""),
            ("14  GPIO14", "", "CLK"),
            ("12  GPIO12", "", "DIO"),
        ),
        caution="Power the displays from the pin labeled 5V, not 3.3V. Leave the pins labeled 0, 2, and 15 alone. They decide how the board starts.",
    ),
)


def get_board(board_id: str | None) -> Board | None:
    for board in BOARDS:
        if board.id == board_id:
            return board
    return None
