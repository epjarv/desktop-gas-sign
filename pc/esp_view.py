"""Setup window for the NodeMCU edition. The board fetches prices after this."""

from __future__ import annotations

import tkinter as tk

from view import ACCENT, FIELD, INK, MUTED, PAPER, SHEET, Command, _entry, _style, _text

PIN_ROWS = (
    ("NodeMCU", "Top display", "Bottom display"),
    ("VIN", "5V", "5V"),
    ("GND", "GND", "GND"),
    ("D1  GPIO5", "CLK", ""),
    ("D2  GPIO4", "DIO", ""),
    ("D5  GPIO14", "", "CLK"),
    ("D6  GPIO12", "", "DIO"),
)


def build(app) -> None:
    _style()
    app.root.configure(bg=PAPER)
    app.root.grid_rowconfigure(0, weight=1)
    app.root.grid_columnconfigure(0, weight=1)
    app.shell = tk.Frame(app.root, bg=PAPER, highlightthickness=0, bd=0)
    app.shell.grid(row=0, column=0, sticky="nsew")
    app.shell.grid_rowconfigure(0, weight=1)
    app.shell.grid_columnconfigure(0, weight=1)
    app.wiring_page = _page(app, _wiring)
    app.upload_page = _page(app, _upload)
    app.setup_page = _page(app, _setup)


def show(app, page: tk.Frame) -> None:
    for other in (app.wiring_page, app.upload_page, app.setup_page):
        if other.winfo_manager():
            other.grid_remove()
    page.grid(row=0, column=0, sticky="nsew")


def _page(app, filler) -> tk.Frame:
    page = tk.Frame(app.shell, bg=PAPER, highlightthickness=0, bd=0)
    page.grid_rowconfigure(0, weight=1)
    page.grid_columnconfigure(0, weight=1)
    body = tk.Frame(page, bg=PAPER, highlightthickness=0, bd=0)
    body.grid(row=0, column=0, sticky="nsew", padx=28, pady=20)
    filler(app, body)
    return page


def _heading(parent, title: str, version: str) -> None:
    row = tk.Frame(parent, bg=SHEET, highlightthickness=0, bd=0)
    row.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    row.grid_columnconfigure(0, weight=1)
    _text(row, title, 22).grid(row=0, column=0, sticky="w")
    _text(row, version, 12, MUTED).grid(row=0, column=1, sticky="e")


def _wiring(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    _heading(body, "Wire the NodeMCU", app.version)
    _text(
        body,
        "Use one header on each display. The second header is the same four pins.",
        13,
    ).grid(row=1, column=0, sticky="w", pady=(0, 12))
    table = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    table.grid(row=2, column=0, sticky="ew")
    for column in range(3):
        table.grid_columnconfigure(column, weight=1)
    for row_index, row in enumerate(PIN_ROWS):
        for column, cell in enumerate(row):
            color = MUTED if row_index == 0 else INK
            size = 12 if row_index == 0 else 13
            _text(table, cell, size, color).grid(row=row_index, column=column, sticky="ew", padx=(0, 16), pady=5)
    actions = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    actions.grid(row=3, column=0, sticky="w", pady=(20, 0))
    Command(actions, "Next", app.show_upload).pack(side="left")
    Command(actions, "Skip", app.show_upload, kind="quiet").pack(side="left", padx=(8, 0))


def _upload(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    _heading(body, "Upload", app.version)
    _text(body, "Plug the NodeMCU into the PC with a data USB cable.", 13).grid(row=1, column=0, sticky="w")
    actions = tk.Frame(body, bg=PAPER, highlightthickness=0, bd=0)
    actions.grid(row=2, column=0, sticky="w", pady=(16, 0))
    app.upload_button = Command(actions, "Upload to NodeMCU", app.start_upload)
    app.upload_button.pack(side="left")
    Command(actions, "Skip", app.show_setup, kind="quiet").pack(side="left", padx=(8, 0))
    _text(body, "Choose Skip if this board already has the program.", 12, MUTED).grid(
        row=3, column=0, sticky="w", pady=(12, 0)
    )
    _text(body, "", 13, variable=app.upload_status).grid(row=4, column=0, sticky="ew", pady=(12, 0))


def _setup(app, body: tk.Frame) -> None:
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(2, weight=1)
    _heading(body, "Wi-Fi and city", app.version)
    _text(body, "The board scans. Pick a network, then it will update on its own.", 13).grid(
        row=1, column=0, sticky="w"
    )
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
    password_shell, app.password_entry = _entry(form, app.password)
    password_shell.grid(row=0, column=1, sticky="ew", pady=6)
    app.password_entry.configure(show="*")
    _text(form, "City", 13).grid(row=1, column=0, sticky="w", padx=(0, 16), pady=6)
    city_shell, app.city_entry = _entry(form, app.city)
    city_shell.grid(row=1, column=1, sticky="ew", pady=6)
    _text(form, "Update every", 13).grid(row=2, column=0, sticky="w", padx=(0, 16), pady=6)
    gap = tk.Frame(form, bg=PAPER, highlightthickness=0, bd=0)
    gap.grid(row=2, column=1, sticky="w", pady=6)
    app.interval_box = tk.Entry(
        gap,
        textvariable=app.interval_text,
        width=6,
        relief="flat",
        bd=0,
        bg=FIELD,
        fg=INK,
        insertbackground=ACCENT,
        highlightthickness=1,
        highlightbackground="#304a6c",
        highlightcolor=ACCENT,
        justify="center",
        font=("Segoe UI", 13),
    )
    app.interval_box.pack(side="left", ipady=6, ipadx=8)
    _text(gap, "minutes", 12, MUTED).pack(side="left", padx=(8, 0))
    app.save_button = Command(form, "Save on board", app.start_save)
    app.save_button.grid(row=3, column=1, sticky="w", pady=(12, 0))
    _text(body, "", 13, variable=app.setup_status).grid(row=5, column=0, sticky="ew", pady=(12, 0))
    _text(
        body,
        "After it connects, unplug the board from the PC and power it from any USB port.",
        12,
        MUTED,
    ).grid(row=6, column=0, sticky="w", pady=(8, 0))
