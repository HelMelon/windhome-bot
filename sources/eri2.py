"""ЕРИС (eri2.nca.by): заброшенные дома и пустующие участки.

Поиск отдаёт только «шапку» объекта (тип, статус, адрес), площадь и
стоимость — в отдельном запросе /forView. Детали тянем только для тех
объектов, что фильтр уже пропустил, иначе каждый цикл — лишние запросы.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import aiohttp

from config import Config
from listing import Listing

SEARCH_URL = "https://eri2.nca.by/api/guest/abandonedObject/search"
DETAIL_URL = "https://eri2.nca.by/api/guest/abandonedObject/{id}/forView"


def _money(detail: dict[str, Any]) -> int | None:
    for state in detail.get("stateList") or []:
        amount = state.get("moneyAmount")
        if amount:
            return int(round(float(amount)))
    return None


def _as_date(ms: Any) -> str | None:
    if not ms:
        return None
    return date.fromtimestamp(float(ms) / 1000.0).strftime("%d.%m.%Y")


async def _post_json(
    session: aiohttp.ClientSession,
    url: str,
    body: dict[str, Any],
    retries: int = 2,
    pause_s: float = 6.0,
) -> dict[str, Any]:
    """POST с повтором на 429: ЕРИС throttlит частые обращения."""
    for attempt in range(retries + 1):
        async with session.post(
            url, json=body, timeout=aiohttp.ClientTimeout(total=40)
        ) as resp:
            if resp.status == 429 and attempt < retries:
                await asyncio.sleep(pause_s * (attempt + 1))
                continue
            resp.raise_for_status()
            return await resp.json(content_type=None)
    raise RuntimeError(f"ЕРИС: 429 после {retries} повторов")


async def fetch(session: aiohttp.ClientSession, cfg: Config) -> list[Listing]:
    """Только то, что есть в выдаче поиска: без деталей и без лишних запросов.

    На 429 не упираемся: цикл идёт раз в 20 минут, следующая попытка будет
    сама и без риска усугубить блокировку.
    """
    body = dict(cfg.eri2.get("search_body") or {})
    payload = await _post_json(session, SEARCH_URL, body, retries=0)
    items = (payload.get("data") or {}).get("content") or []
    region = cfg.filter.region_contains
    public_url = cfg.eri2.get("public_url", DETAIL_URL)
    listings: list[Listing] = []
    for item in items:
        if region not in (item.get("position") or ""):
            continue
        extra = [f"Статус: {item.get('abandonedObjectStateType')}"]
        if item.get("inspectionDate"):
            extra.append(f"Осмотр: {_as_date(item['inspectionDate'])}")
        if item.get("abandonedObjectStateDate"):
            extra.append(f"Решение: {_as_date(item['abandonedObjectStateDate'])}")
        listings.append(
            Listing(
                source="eri2",
                uid=str(item["id"]),
                title=item.get("abandonedObjectType") or "Заброшенный объект",
                url=public_url.format(id=item["id"]),
                address=item.get("position"),
                extra=extra,
            )
        )
    return listings


async def enrich(session: aiohttp.ClientSession, listing: Listing) -> Listing:
    """Дозаполняет карточку площадью и стоимостью — только для показа."""
    payload = await _post_json(
        session, DETAIL_URL.format(id=listing.uid), {}, retries=1, pause_s=15.0
    )
    detail = payload.get("data") or {}
    listing.price_byn = _money(detail)
    if detail.get("square"):
        listing.area_total = float(detail["square"])
    if detail.get("floorCount"):
        listing.extra.append(f"Этажей: {detail['floorCount']}")
    return listing


async def enrich_many(
    session: aiohttp.ClientSession,
    cfg: Config,
    listings: list[Listing],
) -> list[Listing]:
    delay = float(cfg.eri2.get("detail_delay_s", 1.0))
    enriched: list[Listing] = []
    for listing in listings:
        await asyncio.sleep(delay)
        try:
            enriched.append(await enrich(session, listing))
        except Exception:
            continue
    return enriched
