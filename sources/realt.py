"""Realt.by: данные лежат в <script id="__NEXT_DATA__"> обычной HTML-страницы.

Старый бот дёргал /_next/data/<buildId>/...json — этот путь умирает при
каждом деплое сайта. Поэтому страница парсится как есть: buildId не нужен.
Фильтры Realt в URL не применяются (проверено: areaFrom/priceTo игнорируются),
поэтому условия отбираются на нашей стороне по нескольким страницам.
"""

from __future__ import annotations

import json
import re
from typing import Any

import aiohttp

from config import Config
from listing import Listing

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.S,
)
CURRENCY_BY_CODE = {840: "USD", 933: "BYN", 978: "EUR"}


def _parse_page(html: str) -> dict[str, Any]:
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise ValueError("На странице Realt нет __NEXT_DATA__")
    page_props = json.loads(match.group(1))["props"]["pageProps"]
    return page_props


def _price_byn(price: Any, currency_code: Any, rates: dict[str, float]) -> int | None:
    if not price:
        return None
    currency = CURRENCY_BY_CODE.get(currency_code, "BYN")
    rate = 1.0 if currency == "BYN" else rates.get(currency, 0)
    return int(float(price) * rate) if rate else None


def _parse_object(obj: dict[str, Any], rates: dict[str, float]) -> Listing:
    price_byn = _price_byn(obj.get("price"), obj.get("priceCurrency"), rates)
    currency = CURRENCY_BY_CODE.get(obj.get("priceCurrency"), "BYN")
    price_note = None
    if currency != "BYN" and obj.get("price"):
        price_note = f"{int(obj['price']):,} {currency}".replace(",", " ")
    location = obj.get("location") or [None, None]
    lon, lat = (location + [None, None])[:2] if location else (None, None)
    extra = []
    if obj.get("areaLiving"):
        extra.append(f"Жилая: {obj['areaLiving']:g} м²")
    return Listing(
        source="realt",
        uid=str(obj.get("code") or obj.get("uuid")),
        title=obj.get("address") or "Объявление Realt",
        url=f"https://realt.by/gomel-region/sale-flats/object/{obj.get('code')}",
        price_byn=price_byn,
        price_note=price_note,
        address=obj.get("address"),
        area_total=obj.get("areaTotal"),
        area_living=obj.get("areaLiving"),
        rooms=obj.get("rooms"),
        floor=obj.get("storey"),
        floors=obj.get("storeys"),
        seller=obj.get("agencyName") or obj.get("contactName") or "владелец",
        lat=float(lat) if lat else None,
        lon=float(lon) if lon else None,
        extra=extra,
    )


async def fetch(
    session: aiohttp.ClientSession,
    cfg: Config,
    rates: dict[str, float],
) -> list[Listing]:
    base = cfg.realt.get("base_url", "https://realt.by/gomel-region/sale/flats/")
    pages = int(cfg.realt.get("pages", 3))
    found: list[Listing] = []
    for page in range(1, pages + 1):
        url = base if page == 1 else f"{base}?page={page}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            resp.raise_for_status()
            html = await resp.text()
        for obj in _parse_page(html).get("objects") or []:
            if cfg.realt.get("city_only", True) and obj.get("townName") != cfg.filter.city:
                continue
            if obj.get("priceCurrency") == 840:
                usd_cap = cfg.filter.price_max_byn / max(rates.get("USD", 1), 1e-9)
                if (obj.get("price") or 0) > usd_cap * 1.15:
                    continue
            listing = _parse_object(obj, rates)
            if (
                cfg.filter.matches_area(listing.area_total)
                and cfg.filter.matches_price(listing.price_byn)
                and cfg.filter.matches_rooms(listing.rooms)
            ):
                found.append(listing)
    return found
