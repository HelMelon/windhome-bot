"""Район города по координатам объявления.

Ни Kufar, ни Realt не отдают район города в выдаче (у Realt `townDistrictName`
пустой, у Kufar — только числовой id «Город / Район»). Зато у объявлений есть
координаты, а районы Гомеля нарисованы в OSM: полигоны берём один раз через
Nominatim (`polygon_geojson=1`), кэшируем в SQLite на месяц и дальше считаем
точку в полигоне локально. Так на каждый цикл нет ни одного лишнего запроса
к Nominatim — их правила запрещают массовый reverse-geocoding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from store import Store

log = logging.getLogger("windhome.districts")

CACHE_KEY = "districts:{city}"
CACHE_TTL_S = 30 * 24 * 3600
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim требует осмысленный User-Agent приложения, а не браузерный.
NOMINATIM_HEADERS = {"User-Agent": "windhome-bot/1.0 (private housing monitor)"}

DEFAULT_DISTRICTS = {
    "Гомель": ("Железнодорожный", "Новобелицкий", "Советский", "Центральный"),
}
# Настоящий район города — крупный объект; всё мельче 0.02° по стороне bbox это
# POI внутри района (Nominatim на «район, Гомеля» охотно отдаёт мечеть и садики).
MIN_SPAN_DEG = 0.02

_MEMORY: dict[str, dict[str, Any]] = {}


def _in_ring(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Классический ray casting; в GeoJSON точки идут как [lon, lat]."""
    inside = False
    if not ring:
        return False
    j = len(ring) - 1
    for i, (x_i, y_i) in enumerate(ring):
        x_j, y_j = ring[j]
        if (y_i > lat) != (y_j > lat):
            x_cross = (x_j - x_i) * (lat - y_i) / (y_j - y_i) + x_i
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def _in_polygon(lat: float, lon: float, rings: list[list[list[float]]]) -> bool:
    if not rings or not _in_ring(lat, lon, rings[0]):
        return False
    return not any(_in_ring(lat, lon, hole) for hole in rings[1:])


def locate(geometries: dict[str, Any], lat: float | None, lon: float | None) -> str | None:
    """Название района или None, если координат нет / точка вне полигонов."""
    if lat is None or lon is None or not geometries:
        return None
    for name, geometry in geometries.items():
        kind = geometry.get("type")
        if kind == "Polygon" and _in_polygon(lat, lon, geometry.get("coordinates") or []):
            return name
        if kind == "MultiPolygon" and any(
            _in_polygon(lat, lon, polygon)
            for polygon in geometry.get("coordinates") or []
        ):
            return name
    return None


def cached(store: Store, city: str) -> dict[str, Any] | None:
    """Полигоны из памяти процесса или из кэша в БД (без обращения к сети)."""
    if city in _MEMORY:
        return _MEMORY[city]
    raw = store.get(CACHE_KEY.format(city=city))
    if not raw:
        return None
    payload = json.loads(raw)
    if time.time() - payload.get("ts", 0) > CACHE_TTL_S:
        return None
    _MEMORY[city] = payload.get("items") or {}
    return _MEMORY[city]


async def _lookup(session: aiohttp.ClientSession, query: str) -> dict[str, Any] | None:
    params = {
        "q": query,
        "format": "jsonv2",
        "polygon_geojson": "1",
        "limit": "1",
        "accept-language": "ru",
    }
    try:
        async with session.get(
            NOMINATIM_URL,
            params=params,
            headers=NOMINATIM_HEADERS,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            resp.raise_for_status()
            found = await resp.json(content_type=None)
    except Exception as error:
        log.warning("Nominatim не ответил на «%s»: %s", query, error)
        return None
    if not found:
        return None
    best = found[0]
    geometry = best.get("geojson") or {}
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    try:
        south, north, west, east = (float(value) for value in best["boundingbox"])
    except (KeyError, TypeError, ValueError):
        return None
    if best.get("type") != "administrative":
        return None
    if (north - south) < MIN_SPAN_DEG or (east - west) < MIN_SPAN_DEG:
        return None
    return geometry


async def ensure(
    store: Store,
    session: aiohttp.ClientSession,
    city: str,
) -> dict[str, Any]:
    """Полигоны районов города; строит и кэширует, если их ещё нет."""
    ready = cached(store, city)
    if ready:
        return ready
    names = DEFAULT_DISTRICTS.get(city, ())
    if not names:
        return {}
    items: dict[str, Any] = {}
    for name in names:
        geometry = await _lookup(session, f"{name} район, {city}")
        if geometry:
            items[name] = geometry
        await asyncio.sleep(1.2)  # политика Nominatim: не чаще одного запроса в секунду
    if items:
        store.put(
            CACHE_KEY.format(city=city),
            json.dumps({"ts": int(time.time()), "items": items}, ensure_ascii=False),
        )
        _MEMORY[city] = items
        log.info("Полигоны районов %s: %s", city, ", ".join(items))
    return items
