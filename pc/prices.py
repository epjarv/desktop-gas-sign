"""Look up regular and diesel prices from a typed address.

Canada comes first. Quebec averages the three nearest stations. Every
other Canadian address uses the nearest published city average that has
both regular and diesel: an Ontario weekly market, or a Natural Resources
Canada daily city series. The Canada-wide average is not used while a city
series exists.

United States addresses use the nearest EIA city or metro that publishes
its own regular price. UK addresses average the three nearest stations.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import requests

USER_AGENT = "DesktopGasSign/0.1 (DIY desktop fuel sign)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
UK_STATIONS_URL = "https://fuelcosts.co.uk/api/stations"
US_REGIONS_URL = "https://api.thegasnews.love/v1/regions"
US_LATEST_URL = "https://api.thegasnews.love/v1/latest"

QUEBEC_CITIES_URL = "https://www.gasquebec.ca/api/cities"
QUEBEC_CITY_URL = "https://www.gasquebec.ca/api/cities/{slug}"
ONTARIO_CSV_URL = "https://ontario.ca/v1/files/fuel-prices/fueltypesall.csv"
NRCAN_URL = "https://www2.nrcan.gc.ca/eneene/sources/pripri/prices_bycity_e.cfm"
NRCAN_REGULAR = "1"
NRCAN_DIESEL = "5"
NRCAN_CANADA_ID = "66"

UK_REGULAR = "E10"
UK_DIESEL = "B7_STANDARD"
MAX_PRICE_AGE_DAYS = 21
STATION_COUNT = 3
MINOR_UNITS = {"pence_per_litre", "cents_per_litre"}


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _cache_file() -> Path:
    return _data_dir() / ".cache" / "geocode.json"


def _legacy_cache_file() -> Path:
    return _data_dir() / "geocode_cache.json"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

_region_catalog: list[dict] | None = None
_cache: dict[str, tuple[float, object]] = {}
_geo_lock = threading.Lock()
_last_nominatim = 0.0


class PriceError(Exception):
    """A lookup failed in a way the window can show directly."""


@dataclass
class StationQuote:
    name: str
    regular: float | None
    diesel: float | None


@dataclass
class Lookup:
    place_name: str
    unit: str
    summary: str
    stations: list[StationQuote]
    area_regular: float | None = None
    area_diesel: float | None = None


def average(values: list[float]) -> float | None:
    if not values:
        return None
    total = sum((Decimal(str(value)) for value in values), Decimal(0))
    return float(total / Decimal(len(values)))


def format_display(price: float, unit: str) -> str:
    """Four digits and one decimal point, which is all the LED module can show."""
    if unit in MINOR_UNITS:
        if price < 0 or price >= 1000:
            raise ValueError(f"{price} does not fit on a 4-digit display")
        scaled = _quantize(price, "0.1")
        text = f"{scaled:.1f}"
        if scaled < 100:
            text = f"{scaled:05.1f}"
    else:
        if price < 0 or price >= 100:
            raise ValueError(f"{price} does not fit on a 4-digit display")
        if price >= 10:
            scaled = _quantize(price, "0.01")
            text = f"{scaled:.2f}"
        else:
            scaled = _quantize(price, "0.001")
            text = f"{scaled:.3f}"

    digits = [character for character in text if character.isdigit()]
    if len(digits) != 4 or text.count(".") != 1:
        raise ValueError(f"{price} became {text}, which is not 4 digits")
    return text


def lookup_address(address: str) -> Lookup:
    query = " ".join(address.split())
    if not query:
        raise PriceError("Type an address, city, or postal code first.")

    place = _geocode(query)
    country = (place.get("address") or {}).get("country_code", "")
    if country == "ca":
        return _lookup_canada(place)
    if country == "us":
        return _lookup_us(place)
    if country == "gb":
        return _lookup_uk(place)

    country_name = (place.get("address") or {}).get("country") or "that country"
    return Lookup(
        place_name=_place_label(place),
        unit="dollars_per_gallon",
        summary=(
            f"Automatic prices are not set up for {country_name} yet. "
            "Type the regular and diesel price from up to three nearby stations. "
            "The sign shows the average of whatever you enter."
        ),
        stations=[],
    )


def _quantize(price: float, places: str) -> Decimal:
    return Decimal(str(price)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def get_json(url: str, params: dict | None = None) -> dict:
    try:
        response = SESSION.get(url, params=params, timeout=25)
    except requests.Timeout as exc:
        raise PriceError("The price service timed out. Check your connection and try again.") from exc
    except requests.RequestException as exc:
        raise PriceError("Could not reach the price service. Check your connection and try again.") from exc

    if response.status_code == 429:
        raise PriceError("The price service asked us to slow down. Wait a minute and try again.")
    if response.status_code >= 400:
        raise PriceError(f"The price service returned an error ({response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise PriceError("The price service sent a response that was not a price list.") from exc
    if not isinstance(payload, dict):
        raise PriceError("The price service sent a response that was not a price list.")
    return payload


def _pace_nominatim() -> None:
    global _last_nominatim
    wait = 1.1 - (time.time() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.time()


class _MapBusy(Exception):
    """Nominatim timed out or asked us to slow down."""


MAP_BUSY = (
    "That city could not be located. The map service is busy right now. "
    "Wait a minute and try again."
)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_geo_cache(cache: dict) -> None:
    path = _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache), encoding="utf-8")


def _nominatim(params: dict) -> list:
    with _geo_lock:
        _pace_nominatim()
        try:
            response = SESSION.get(NOMINATIM_URL, params=params, timeout=(5, 12))
        except (requests.Timeout, requests.RequestException) as exc:
            raise _MapBusy from exc
    if response.status_code == 429 or response.status_code >= 500 or not response.ok:
        raise _MapBusy
    try:
        results = response.json()
    except ValueError as exc:
        raise _MapBusy from exc
    if not isinstance(results, list):
        raise _MapBusy
    return results


def _slim_place(result: dict) -> dict:
    return {
        "lat": result.get("lat"),
        "lon": result.get("lon"),
        "display_name": result.get("display_name") or "",
        "address": result.get("address") or {},
    }


def _geocode(query: str) -> dict:
    """Locate the typed city once and reuse that point on later lookups."""
    key = f"q|{_fold(query)}"
    cache = _read_geo_cache()
    saved = cache.get(key)
    if isinstance(saved, dict) and saved.get("lat") is not None and saved.get("lon") is not None:
        return saved
    try:
        results = _nominatim(
            {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 1}
        )
    except _MapBusy as exc:
        raise PriceError(MAP_BUSY) from exc
    if not results:
        raise PriceError("No place found. Try a city and state, or a postal code.")
    place = _slim_place(results[0])
    cache[key] = place
    _write_geo_cache(cache)
    return place


def _place_label(place: dict) -> str:
    address = place.get("address") or {}
    locality = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("suburb")
    )
    state = address.get("state")
    country = address.get("country")
    parts = [part for part in (locality, state, country) if part]
    if parts:
        return ", ".join(parts)
    return place.get("display_name") or "That place"


def _norm(name: str) -> str:
    cleaned = name.casefold().replace("–", "-").replace("—", "-").replace(".", "").replace("'", "")
    return " ".join(cleaned.split())


def _fold(name: str) -> str:
    text = _norm(name)
    for source, target in (
        ("é", "e"),
        ("è", "e"),
        ("ê", "e"),
        ("ë", "e"),
        ("à", "a"),
        ("â", "a"),
        ("ô", "o"),
        ("î", "i"),
        ("ï", "i"),
        ("ù", "u"),
        ("û", "u"),
        ("ç", "c"),
    ):
        text = text.replace(source, target)
    return text


def _remember(key: str, seconds: int, loader):
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < seconds:
        return cached[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def _get_text(url: str, params: dict | None = None, timeout: int = 20) -> str:
    try:
        response = SESSION.get(url, params=params, timeout=timeout)
    except requests.Timeout as exc:
        raise PriceError("The price service timed out. Check your connection and try again.") from exc
    except requests.RequestException as exc:
        raise PriceError("Could not reach the price service. Check your connection and try again.") from exc
    if response.status_code == 429:
        raise PriceError("The price service asked us to slow down. Wait a minute and try again.")
    if response.status_code >= 400:
        raise PriceError(f"The price service returned an error ({response.status_code}).")
    response.encoding = response.apparent_encoding or response.encoding or "utf-8"
    return response.text


def _cents(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    if price < 10:
        price *= 100
    return price


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return _miles(lat1, lon1, lat2, lon2) * 1.609344


def _km_text(distance: float) -> str:
    if distance < 10:
        return f"{distance:.1f} km"
    return f"{round(distance)} km"


def _as_point(saved) -> tuple[float, float] | None:
    if isinstance(saved, (list, tuple)) and len(saved) >= 2 and saved[0] is not None and saved[1] is not None:
        return float(saved[0]), float(saved[1])
    return None


def _bundled_cache() -> dict:
    if getattr(sys, "frozen", False):
        path = Path(getattr(sys, "_MEIPASS")) / "cache" / "geocode.json"
    else:
        path = Path(__file__).resolve().parent / ".cache" / "geocode.json"
    return _read_json(path) if path.exists() else {}


def _read_geo_cache() -> dict:
    path = _cache_file()
    legacy = _legacy_cache_file()
    if path.exists():
        data = _read_json(path)
    else:
        data = _read_json(legacy) if legacy.exists() else {}
    seed = _bundled_cache()
    added = False
    for key, value in seed.items():
        if key.startswith("q|") or key in data:
            continue
        data[key] = value
        added = True
    if added or (data and not path.exists()):
        _write_geo_cache(data)
    return data


def _catalog_points(queries: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """Saved coordinates only. A lookup never geocodes the published-city list."""
    cache = _read_geo_cache()
    points: dict[str, tuple[float, float]] = {}
    seen: set[str] = set()
    for query, country in queries:
        key = f"{country}|{_fold(query)}"
        if key in seen:
            continue
        seen.add(key)
        point = _as_point(cache.get(key))
        if point is not None:
            points[key] = point
    return points


def _lookup_canada(place: dict) -> Lookup:
    province = _fold((place.get("address") or {}).get("state") or "")
    if province == "quebec":
        try:
            quebec = _lookup_quebec(place)
        except PriceError:
            quebec = None
        if quebec is not None:
            return quebec
    return _lookup_nearest_canadian_average(place)


def _quebec_cities() -> list[dict]:
    def load():
        payload = get_json(QUEBEC_CITIES_URL, {"include": "prices"})
        cities = payload.get("cities")
        if not isinstance(cities, list):
            raise PriceError("The Quebec price feed did not list any cities.")
        return cities

    return _remember("quebec-cities", 30 * 60, load)


def _lookup_quebec(place: dict) -> Lookup | None:
    latitude = float(place["lat"])
    longitude = float(place["lon"])
    cities = [
        city
        for city in _quebec_cities()
        if city.get("lat") is not None and city.get("lng") is not None and city.get("slug")
    ]
    cities.sort(key=lambda city: _km(latitude, longitude, float(city["lat"]), float(city["lng"])))

    found = []
    seen = set()
    for city in cities[:2]:
        detail = get_json(QUEBEC_CITY_URL.format(slug=city["slug"]))
        for station in detail.get("stations") or []:
            station_id = station.get("stationId")
            if station_id in seen:
                continue
            seen.add(station_id)
            if station.get("lat") is None or station.get("lng") is None:
                continue
            regular = _cents(station.get("prixOrdinaire"))
            diesel = _cents(station.get("prixDiesel"))
            if regular is None and diesel is None:
                continue
            distance = _km(latitude, longitude, float(station["lat"]), float(station["lng"]))
            name = station.get("brand") or station.get("name") or "Station"
            found.append(
                {
                    "name": name,
                    "regular": regular,
                    "diesel": diesel,
                    "distance": distance,
                    "stale": bool(station.get("stale")),
                }
            )

    fresh = [station for station in found if not station["stale"]]
    pool = fresh if fresh else found
    pool.sort(key=lambda station: station["distance"])
    chosen = pool[:STATION_COUNT]
    if not chosen:
        return None

    lines = [f"Average of the {len(chosen)} nearest stations."]
    quotes = []
    for station in chosen:
        bits = []
        if station["regular"] is not None:
            bits.append(f"regular {station['regular']:.1f}")
        if station["diesel"] is not None:
            bits.append(f"diesel {station['diesel']:.1f}")
        label = f"{station['name']} · {station['distance']:.1f} km"
        lines.append(f"{label}: {', '.join(bits)}")
        quotes.append(StationQuote(name=label, regular=station["regular"], diesel=station["diesel"]))
    diesel_count = sum(station.diesel is not None for station in quotes)
    regular_count = sum(station.regular is not None for station in quotes)
    if diesel_count == 0:
        lines.append("None of these stations published a diesel price.")
    elif diesel_count < len(quotes):
        lines.append(f"Diesel was published by {diesel_count} of these stations.")
    if regular_count == 0:
        lines.append("None of these stations published a regular price.")
    elif regular_count < len(quotes):
        lines.append(f"Regular was published by {regular_count} of these stations.")
    lines.append(
        "Prices are cents per litre. "
        "Fuel-price data from Régie Essence Québec (Régie de l'énergie du Québec), presented by Gas Quebec (gasquebec.ca)."
    )
    return Lookup(
        place_name=_place_label(place),
        unit="cents_per_litre",
        summary="\n".join(lines),
        stations=quotes,
    )


def _ontario_rows() -> list[dict]:
    def load_csv() -> str:
        return _get_text(ONTARIO_CSV_URL, timeout=40)

    text = _remember("ontario-csv", 6 * 60 * 60, load_csv)
    if text.startswith("\ufeff"):
        text = text[1:]
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise PriceError("The Ontario price file was empty.")
    return rows


def _latest_ontario_rows(rows: list[dict]) -> tuple[dict, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        fuel = (row.get("Fuel Type") or "").strip()
        date = (row.get("Date") or "").strip()
        if fuel in {"Regular Unleaded Gasoline", "Diesel"} and date:
            if fuel not in latest or date > latest[fuel]["Date"]:
                latest[fuel] = row
    regular_row = latest.get("Regular Unleaded Gasoline")
    diesel_row = latest.get("Diesel")
    if regular_row is None or diesel_row is None:
        raise PriceError("The Ontario price file had no regular or diesel rows.")
    return regular_row, diesel_row


def _ontario_place_query(column: str) -> tuple[str, str]:
    folded = _fold(column)
    special = {
        "toronto west/ouest": ("Toronto West", "Etobicoke, Toronto, Ontario, Canada"),
        "toronto east/est": ("Toronto East", "Scarborough, Toronto, Ontario, Canada"),
        "st catharines": ("St. Catharines", "St. Catharines, Ontario, Canada"),
        "sault saint marie": ("Sault Ste. Marie", "Sault Ste. Marie, Ontario, Canada"),
    }
    if folded in special:
        return special[folded]
    label = column.split("/")[0].strip()
    return label, f"{label}, Ontario, Canada"


def _published_canadian_places() -> list[dict]:
    def load() -> list[dict]:
        nrcan = []
        queries: list[tuple[str, str]] = []
        for location_id, name in _nrcan_locations():
            if location_id == NRCAN_CANADA_ID or _fold(name) == "canada":
                continue
            query = f"{name}, Canada"
            nrcan.append((location_id, name, query))
            queries.append((query, "ca"))

        rows = _ontario_rows()
        regular_row, diesel_row = _latest_ontario_rows(rows)
        ontario = []
        for column in rows[0]:
            if not column:
                continue
            folded = _fold(str(column))
            if folded in {"date", "fuel type", "type de carburant"} or "average" in folded or "moyenne" in folded:
                continue
            regular = _cents(regular_row.get(column))
            diesel = _cents(diesel_row.get(column))
            if regular is None or diesel is None:
                continue
            label, query = _ontario_place_query(str(column))
            ontario.append((label, query, regular, diesel, regular_row.get("Date") or ""))
            queries.append((query, "ca"))

        points = _catalog_points(queries)
        places = []
        for location_id, name, query in nrcan:
            point = points.get(f"ca|{_fold(query)}")
            if point is None:
                continue
            places.append(
                {
                    "label": name,
                    "lat": point[0],
                    "lon": point[1],
                    "source": "nrcan",
                    "location_id": location_id,
                }
            )
        for label, query, regular, diesel, surveyed in ontario:
            point = points.get(f"ca|{_fold(query)}")
            if point is None:
                continue
            places.append(
                {
                    "label": label,
                    "lat": point[0],
                    "lon": point[1],
                    "source": "ontario",
                    "regular": regular,
                    "diesel": diesel,
                    "surveyed": surveyed,
                }
            )
        if not places:
            raise PriceError(
                "No Canadian city price series could be located. "
                "The map service may be busy. Wait a minute and try again."
            )
        return places

    return _remember("canada-places", 24 * 60 * 60, load)


def _lookup_nearest_canadian_average(place: dict) -> Lookup:
    latitude = float(place["lat"])
    longitude = float(place["lon"])
    ranked = sorted(
        _published_canadian_places(),
        key=lambda candidate: _km(latitude, longitude, candidate["lat"], candidate["lon"]),
    )
    for candidate in ranked:
        distance = _km(latitude, longitude, candidate["lat"], candidate["lon"])
        if candidate["source"] == "ontario":
            return _canadian_area_lookup(
                place,
                candidate["label"],
                distance,
                candidate["regular"],
                candidate["diesel"],
                (
                    f"Weekly retail average for {candidate['label']}, surveyed {candidate['surveyed']} "
                    "by the Government of Ontario. This is a market average, not three stations."
                ),
            )
        try:
            regular, regular_date = _nrcan_price(NRCAN_REGULAR, candidate["location_id"])
            diesel, diesel_date = _nrcan_price(NRCAN_DIESEL, candidate["location_id"])
        except PriceError:
            continue
        return _canadian_area_lookup(
            place,
            candidate["label"],
            distance,
            regular,
            diesel,
            (
                "Daily retail average from Natural Resources Canada. "
                f"Regular was surveyed {regular_date} and diesel {diesel_date}. "
                "This is a city average, not three stations."
            ),
        )
    raise PriceError("No nearby Canadian city has published both regular and diesel.")


def _canadian_area_lookup(
    place: dict,
    series_name: str,
    distance: float,
    regular: float,
    diesel: float,
    detail: str,
) -> Lookup:
    summary = (
        f"Nearest published average: {series_name}, {_km_text(distance)}.\n"
        f"{detail} Prices are cents per litre. "
        "Type up to three stations below and the sign will average those instead."
    )
    return Lookup(
        place_name=_place_label(place),
        unit="cents_per_litre",
        summary=summary,
        stations=[],
        area_regular=regular,
        area_diesel=diesel,
    )


def _average_columns(row: dict, columns: list[str]) -> float | None:
    values = []
    for column in columns:
        price = _cents(row.get(column))
        if price is not None:
            values.append(price)
    return average(values)


def _nrcan_locations() -> list[tuple[str, str]]:
    def load():
        html = _get_text(NRCAN_URL, {"PriceYear": "0", "ProductID": NRCAN_REGULAR})
        found = re.findall(r'name="locationID"[^>]*value="(\d+)"[^>]*>\s*([^<]*)', html)
        locations = []
        for location_id, name in found:
            cleaned = " ".join(name.split())
            if location_id == NRCAN_CANADA_ID and not cleaned:
                cleaned = "Canada"
            if cleaned:
                locations.append((location_id, cleaned))
        if not any(location_id == NRCAN_CANADA_ID for location_id, _name in locations):
            locations.append((NRCAN_CANADA_ID, "Canada"))
        if len(locations) < 10:
            raise PriceError("The Canadian price page did not list its cities.")
        return locations

    return _remember("nrcan-cities", 24 * 60 * 60, load)


def _nrcan_price(product_id: str, location_id: str) -> tuple[float, str]:
    def load():
        html = _get_text(
            NRCAN_URL,
            {"PriceYear": "0", "ProductID": product_id, "locationID": location_id},
        )
        rows = re.findall(r"<tr[\s\S]*?</tr>", html, flags=re.I)
        dated = []
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row, flags=re.I)
            cleaned = []
            for cell in cells:
                text = re.sub(r"<[^>]+>", "", cell)
                text = " ".join(text.split())
                if text:
                    cleaned.append(text)
            if cleaned and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned[0]):
                dated.append(cleaned)
        for cells in reversed(dated):
            if len(cells) < 2:
                continue
            try:
                price = float(cells[1].replace(",", ""))
            except ValueError:
                continue
            if 20 <= price <= 500:
                return price, cells[0]
        raise PriceError("The Canadian price table had no recent regular or diesel figure.")

    return _remember(f"nrcan-{product_id}-{location_id}", 6 * 60 * 60, load)


def _state_name_for_region(catalog: list[dict], code: str) -> str:
    parts = code.split("-")
    if len(parts) < 2:
        return ""
    state_code = f"{parts[0]}-{parts[1]}"
    for row in catalog:
        if row.get("code") == state_code and row.get("kind") == "state":
            return str(row.get("name") or "")
    return ""


def _lookup_us(place: dict) -> Lookup:
    latitude = float(place["lat"])
    longitude = float(place["lon"])
    catalog = _us_regions()
    wanted = []
    for row in catalog:
        if row.get("kind") not in {"city", "metro"}:
            continue
        scope = row.get("scope") or {}
        if scope.get("isFallback") is not False:
            continue
        state_name = _state_name_for_region(catalog, str(row.get("code") or ""))
        name = str(row.get("name") or "").replace("–", "-").replace("—", "-")
        query = f"{name}, {state_name}, USA" if state_name else f"{name}, USA"
        wanted.append((query, row))
    points = _catalog_points([(query, "us") for query, _row in wanted])
    ranked = []
    for query, row in wanted:
        point = points.get(f"us|{_fold(query)}")
        if point is None:
            continue
        ranked.append((_km(latitude, longitude, point[0], point[1]), row))
    if not ranked:
        raise PriceError(
            "No US city price series could be located. "
            "The map service may be busy. Wait a minute and try again."
        )
    ranked.sort(key=lambda item: item[0])
    distance, matched = ranked[0]
    latest = _us_latest(str(matched["code"]))
    fuels = latest.get("fuels") or {}
    regular = (fuels.get("gasoline_regular") or {}).get("price")
    diesel = (fuels.get("diesel") or {}).get("price")
    if regular is None or diesel is None:
        raise PriceError(f"{matched.get('name')} did not include both regular and diesel.")

    scopes = latest.get("scopes") or {}
    regular_note = (scopes.get("gasoline_regular") or {}).get("note") or f"{matched.get('name')} average"
    diesel_note = (scopes.get("diesel") or {}).get("note") or regular_note
    surveyed = _pretty_date(latest.get("asOf") or "")
    if regular_note == diesel_note:
        detail = regular_note
    else:
        detail = f"Regular: {regular_note} Diesel: {diesel_note}"

    summary = (
        f"Nearest published average: {matched.get('name')}, {_km_text(distance)}.\n"
        f"{detail} Survey date {surveyed}. "
        "These are weekly retail averages from the U.S. Energy Information Administration, "
        "not the pumps at three stations. "
        "Type up to three stations below and the sign will average those instead."
    )
    return Lookup(
        place_name=_place_label(place),
        unit="dollars_per_gallon",
        summary=summary,
        stations=[],
        area_regular=float(regular),
        area_diesel=float(diesel),
    )


def _us_regions() -> list[dict]:
    global _region_catalog
    if _region_catalog is None:
        payload = get_json(US_REGIONS_URL)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise PriceError("The US price feed did not list any places.")
        _region_catalog = rows
    return _region_catalog


def _us_latest(code: str) -> dict:
    payload = get_json(US_LATEST_URL, {"region": code})
    data = payload.get("data")
    if not isinstance(data, dict) or "fuels" not in data:
        raise PriceError(f"The US price feed had no prices for {code}.")
    return data


def _lookup_uk(place: dict) -> Lookup:
    latitude = float(place["lat"])
    longitude = float(place["lon"])
    chosen = _uk_stations(latitude, longitude, radius=15)
    if len(chosen) < STATION_COUNT:
        wider = _uk_stations(latitude, longitude, radius=40)
        if len(wider) > len(chosen):
            chosen = wider
    chosen = chosen[:STATION_COUNT]

    if not chosen:
        return Lookup(
            place_name=_place_label(place),
            unit="pence_per_litre",
            summary=(
                "No recent unleaded or diesel prices were published near that place. "
                "Type up to three stations below and the sign will average those."
            ),
            stations=[],
        )

    lines = [f"Average of {len(chosen)} nearest stations with a price from the last {MAX_PRICE_AGE_DAYS} days."]
    quotes = []
    for station in chosen:
        bits = []
        if station["regular"] is not None:
            bits.append(f"regular {station['regular']:.1f}")
        if station["diesel"] is not None:
            bits.append(f"diesel {station['diesel']:.1f}")
        lines.append(f"{station['label']}: {', '.join(bits)}")
        quotes.append(
            StationQuote(name=station["label"], regular=station["regular"], diesel=station["diesel"])
        )
    lines.append(
        "Regular is E10 unleaded and diesel is standard B7, in pence per litre. "
        "Source: UK Government Fuel Finder, via FuelCosts.co.uk."
    )
    return Lookup(
        place_name=_place_label(place),
        unit="pence_per_litre",
        summary="\n".join(lines),
        stations=quotes,
    )


def _uk_stations(latitude: float, longitude: float, radius: int) -> list[dict]:
    payload = get_json(
        UK_STATIONS_URL,
        {
            "lat": latitude,
            "lon": longitude,
            "radius": radius,
            "sort": "distance",
            "perPage": 50,
            "page": 1,
        },
    )
    rows = payload.get("stations")
    if not isinstance(rows, list):
        raise PriceError("The UK price feed did not return a station list.")

    found = []
    for station in rows:
        if station.get("is_permanently_closed") or station.get("is_temporarily_closed"):
            continue
        if station.get("isActive") is False:
            continue
        regular, regular_age = _uk_grade(station, UK_REGULAR)
        diesel, diesel_age = _uk_grade(station, UK_DIESEL)
        if regular is None and diesel is None:
            continue
        distance = _station_miles(station, latitude, longitude)
        age_days = _youngest_age(regular_age, diesel_age)
        label = station.get("trading_name") or station.get("brand_name") or station.get("name") or "Station"
        if distance is not None:
            label = f"{label} · {distance:.1f} mi"
        if age_days is not None:
            label = f"{label} · {age_days}d"
        found.append(
            {
                "label": label,
                "regular": regular,
                "diesel": diesel,
                "distance": distance if distance is not None else 9999,
            }
        )

    found.sort(key=lambda item: item["distance"])
    return found[:STATION_COUNT]


def _uk_grade(station: dict, fuel_code: str) -> tuple[float | None, int | None]:
    for item in station.get("prices") or []:
        if item.get("fuel_type") != fuel_code or item.get("price") is None:
            continue
        price = float(item["price"])
        if price < 10:
            price *= 100
        age = _age_days(item.get("price_last_updated"))
        if age is not None and age > MAX_PRICE_AGE_DAYS:
            return None, None
        return price, age
    return None, None


def _youngest_age(regular_age: int | None, diesel_age: int | None) -> int | None:
    ages = [age for age in (regular_age, diesel_age) if age is not None]
    if not ages:
        return None
    return min(ages)


def _age_days(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    elapsed = datetime.now(timezone.utc) - stamp
    return max(0, int(elapsed.total_seconds() // 86400))


def _station_miles(station: dict, latitude: float, longitude: float) -> float | None:
    raw = station.get("distance")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    station_lat = station.get("lat")
    station_lon = station.get("lon")
    location = station.get("location") or {}
    if station_lat is None:
        station_lat = location.get("latitude")
    if station_lon is None:
        station_lon = location.get("longitude")
    if station_lat is None or station_lon is None:
        return None
    return _miles(latitude, longitude, float(station_lat), float(station_lon))


def _miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius = 3958.8
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * earth_radius * asin(sqrt(a))


def _pretty_date(iso: str) -> str:
    if not iso:
        return "unknown"
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return f"{stamp.strftime('%B')} {stamp.day}, {stamp.year}"
