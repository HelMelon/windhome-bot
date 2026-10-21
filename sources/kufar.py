"""Kufar: публичный search-API, отдаёт JSON без авторизации.

Ключи фильтров: prc — цена в копейках, sls — площадь м², rms — комнаты,
gtsy — гео-слаг. Часть значений (площадь) сайт иногда не отдаёт в
ad_parameters, поэтому карточка терпит None и просто не показывает строку.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from config import Config
from listing import Listing

API = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
GOMEL_GEO = (
    "country-belarus~province-gomelskaja_oblast~locality-gomel"
)


def _is_blank(value: Any) -> bool:
    """Пустая строка, список пустых строк или пустой список — это «нет значения»."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return not value or all(_is_blank(item) for item in value)
    if isinstance(value, dict):
        return not value
    return False


def _params(ad: dict[str, Any]) -> dict[str, Any]:
    """Склеивает ad_parameters и account_parameters в один словарь по label.

    Kufar кладёт человекочитаемое значение в vl, машинное — в v. Для чисел и
    координат vl приходит пустой строкой или списком пустых строк, поэтому
    «пустой» vl не должен затирать v.
    """
    merged: dict[str, Any] = {}
    for group in ("ad_parameters", "account_parameters"):
        for param in ad.get(group) or []:
            label = param.get("pl") or param.get("p")
            readable = param.get("vl")
            machine = param.get("v")
            merged[label] = machine if _is_blank(readable) else readable
    return merged


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _coords(value: Any) -> tuple[float | None, float | None]:
    """Координаты Kufar приходят как [lon, lat] либо как dict."""
    if isinstance(value, dict):
        lat, lon = value.get("lat"), value.get("lng") or value.get("lon")
        return (float(lat), float(lon)) if lat and lon else (None, None)
    if isinstance(value, list) and len(value) >= 2:
        try:
            lon, lat = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return (None, None)
        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else (None, None)
    return (None, None)


def _to_int(value: Any) -> int | None:
    text = str(value).strip() if value is not None else ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _build_url(cfg: Config) -> str:
    price_max = cfg.filter.price_max_byn * 100
    common = (
        f"{API}?cat={cfg.kufar.get('category', 1010)}&cur=BYR"
        f"&gtsy={GOMEL_GEO}&lang=ru&size={cfg.kufar.get('page_size', 30)}&typ=sell"
    )
    query = common + f"&prc=r%3A0%2C{price_max}"
    if cfg.filter.area_min:
        query += f"&sls=r%3A{int(cfg.filter.area_min)}%2C1000"
    if cfg.filter.rooms:
        rooms = "%2C".join(str(r) for r in cfg.filter.rooms)
        query += f"&rms=v.or%3A{rooms}"
    return query


def _parse(ad: dict[str, Any]) -> Listing:
    params = _params(ad)
    area_total = params.get("Общая площадь, м²")
    area_living = params.get("Жилая площадь, м²")
    rooms = _to_int(params.get("Комнат"))
    price_kopecks = ad.get("price_byn")
    price_byn = int(price_kopecks) // 100 if price_kopecks else None
    lat, lon = _coords(params.get("Координаты"))
    address = params.get("Адрес") or params.get("Город / Район")
    seller = "агентство" if params.get("Имя") else "владелец"
    balcony = params.get("Балкон")
    title_parts = []
    if rooms:
        title_parts.append(f"{rooms}-к.")
    if area_total:
        title_parts.append(f"{float(area_total):g} м²")
    return Listing(
        source="kufar",
        uid=str(ad.get("ad_id")),
        title=", ".join(title_parts) or (ad.get("subject") or "Объявление Kufar"),
        url=ad.get("ad_link") or f"https://www.kufar.by/item/{ad.get('ad_id')}",
        price_byn=price_byn,
        address=address,
        area_total=float(area_total) if area_total else None,
        area_living=float(area_living) if area_living else None,
        rooms=rooms,
        floor=_to_int(_first(params.get("Этаж"))),
        floors=_to_int(_first(params.get("Количество этажей"))),
        seller=seller,
        lat=lat,
        lon=lon,
        extra=[item for item in [balcony and f"Балкон: {balcony}"] if item],
    )


async def fetch(session: aiohttp.ClientSession, cfg: Config) -> list[Listing]:
    headers = {"Accept": "application/json"}
    async with session.get(
        _build_url(cfg),
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=40),
    ) as resp:
        resp.raise_for_status()
        payload = await resp.json(content_type=None)
    listings = [_parse(ad) for ad in payload.get("ads") or []]
    return [
        item
        for item in listings
        if cfg.filter.matches_area(item.area_total)
        and cfg.filter.matches_price(item.price_byn)
        and cfg.filter.matches_rooms(item.rooms)
    ]
