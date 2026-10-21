"""Build a compact place table for the ESP8266 sketch.

Coordinates choose the nearest published series. The sketch still downloads
the live price. This script is not part of the sign's runtime.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "pc" / ".cache" / "geocode.json"
OUT = ROOT / "esp8266" / "gas_sign_nodemcu" / "places.h"
HEADERS = {"User-Agent": "DesktopGasSign/1.11 (DIY desktop fuel sign)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fold(text: str) -> str:
    return " ".join(text.lower().split())


def load_cache() -> dict:
    if not CACHE.is_file():
        return {}
    payload = json.loads(CACHE.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def point(cache: dict, country: str, query: str) -> tuple[float, float] | None:
    saved = cache.get(f"{country}|{fold(query)}")
    if isinstance(saved, dict) and saved.get("lat") is not None:
        return float(saved["lat"]), float(saved["lon"])
    saved = cache.get(f"q|{fold(query)}")
    if isinstance(saved, dict) and saved.get("lat") is not None:
        return float(saved["lat"]), float(saved["lon"])
    return None


def geocode(cache: dict, country: str, query: str) -> tuple[float, float]:
    found = point(cache, country, query)
    if found:
        return found
    time.sleep(1.1)
    response = SESSION.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 1},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise RuntimeError(f"no geocode for {query}")
    lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
    cache[f"{country}|{fold(query)}"] = {"lat": lat, "lon": lon}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return lat, lon


def c_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def nrcan_places(cache: dict) -> list[str]:
    html = SESSION.get(
        "https://www2.nrcan.gc.ca/eneene/sources/pripri/prices_bycity_e.cfm",
        params={"PriceYear": "0", "ProductID": "1"},
        timeout=40,
    ).text
    found = re.findall(r'name="locationID"[^>]*value="(\d+)"[^>]*>\s*([^<]*)', html)
    lines = []
    for location_id, name in found:
        cleaned = " ".join(name.split())
        if location_id == "66" or fold(cleaned) == "canada" or not cleaned:
            continue
        lat, lon = geocode(cache, "ca", f"{cleaned}, Canada")
        lines.append(
            f"  {{{location_id}, {lat:.5f}f, {lon:.5f}f, {c_string(cleaned)}}},"
        )
        print("nrcan", cleaned)
    return lines


def ontario_places(cache: dict) -> list[str]:
    text = SESSION.get("https://ontario.ca/v1/files/fuel-prices/fueltypesall.csv", timeout=60).text
    if text.startswith("\ufeff"):
        text = text[1:]
    header = text.splitlines()[0].split(",")
    lines = []
    for column in header:
        folded = fold(column)
        if not column or folded in {"date", "fuel type", "type de carburant"} or "average" in folded or "moyenne" in folded:
            continue
        label = column.split("/")[0].strip()
        special = {
            "toronto west/ouest": "Etobicoke, Toronto, Ontario, Canada",
            "toronto east/est": "Scarborough, Toronto, Ontario, Canada",
            "st. catharine's": "St. Catharines, Ontario, Canada",
            "sault saint marie": "Sault Ste. Marie, Ontario, Canada",
        }
        query = special.get(fold(column), f"{label}, Ontario, Canada")
        lat, lon = geocode(cache, "ca", query)
        lines.append(f"  {{{c_string(column)}, {lat:.5f}f, {lon:.5f}f, {c_string(label)}}},")
        print("ontario", column)
    return lines


def us_places(cache: dict) -> list[str]:
    payload = SESSION.get("https://api.thegasnews.love/v1/regions", timeout=40).json()
    lines = []
    for row in payload.get("data") or []:
        if row.get("kind") not in {"city", "metro"}:
            continue
        if (row.get("scope") or {}).get("isFallback") is not False:
            continue
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        state = ""
        parts = code.split("-")
        if len(parts) >= 2:
            state_code = f"{parts[0]}-{parts[1]}"
            for item in payload["data"]:
                if item.get("code") == state_code and item.get("kind") == "state":
                    state = str(item.get("name") or "")
                    break
        query = f"{name}, {state}, USA" if state else f"{name}, USA"
        lat, lon = geocode(cache, "us", query)
        lines.append(f"  {{{c_string(code)}, {lat:.5f}f, {lon:.5f}f, {c_string(name)}}},")
        print("us", code, name)
    return lines


def main() -> None:
    cache = load_cache()
    nrcan = nrcan_places(cache)
    ontario = ontario_places(cache)
    us = us_places(cache)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    text = "\n".join(
        [
            "#pragma once",
            "// Nearest-series coordinates. Prices are downloaded live.",
            "struct NrcanPlace { int id; float lat; float lon; const char *name; };",
            "struct NamedPlace { const char *key; float lat; float lon; const char *name; };",
            "static const NrcanPlace NRCAN_PLACES[] = {",
            *nrcan,
            "};",
            f"static const int NRCAN_COUNT = {len(nrcan)};",
            "static const NamedPlace ONTARIO_PLACES[] = {",
            *ontario,
            "};",
            f"static const int ONTARIO_COUNT = {len(ontario)};",
            "static const NamedPlace US_PLACES[] = {",
            *us,
            "};",
            f"static const int US_COUNT = {len(us)};",
            "",
        ]
    )
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT, "nrcan", len(nrcan), "ontario", len(ontario), "us", len(us))


if __name__ == "__main__":
    main()
