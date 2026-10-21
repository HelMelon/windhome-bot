"""Сбор объявлений со всех включённых источников.

Ошибка одного источника не должна гасить цикл: остальные продолжают
работать, а текст ошибки уходит в лог и в /status.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import aiohttp

from config import Config
from listing import Listing
from rates import load_rates
from sources import eri2, kufar, realt
from store import Store
import districts as district_index

SOURCE_TITLES = {"kufar": "Kufar", "realt": "Realt", "eri2": "ЕРИС (заброшки)"}


@dataclass
class FetchResult:
    listings: list[Listing] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    rates: dict[str, float] = field(default_factory=dict)


async def fetch_all(
    cfg: Config,
    store: Store,
    session: aiohttp.ClientSession,
    only: dict[str, bool] | None = None,
) -> FetchResult:
    """only — маска источников: {'kufar': True, 'realt': False, ...}."""
    result = FetchResult()
    enabled = only if only is not None else cfg.sources
    result.rates = await load_rates(
        session, store, cfg.realt.get("fallback_usd_byn", 3.0)
    )
    tasks: dict[str, asyncio.Task] = {}
    if enabled.get("kufar"):
        tasks["kufar"] = asyncio.create_task(kufar.fetch(session, cfg))
    if enabled.get("realt"):
        tasks["realt"] = asyncio.create_task(realt.fetch(session, cfg, result.rates))
    if enabled.get("eri2"):
        tasks["eri2"] = asyncio.create_task(eri2.fetch(session, cfg))
    for name, task in tasks.items():
        try:
            result.listings.extend(await task)
        except Exception as error:  # сеть, смена формата, антибот — всё сюда
            result.errors[name] = f"{type(error).__name__}: {error}"[:200]
    await _apply_districts(cfg, store, session, result)
    return result


async def _apply_districts(
    cfg: Config,
    store: Store,
    session: aiohttp.ClientSession,
    result: "FetchResult",
) -> None:
    """Проставляет район города по координатам и режет выдачу по нему.

    Полигоны районов лежат в кэше БД; к Nominatim идём только когда фильтр по
    району включён, а кэша ещё нет.
    """
    if cfg.filter.districts:
        geometries = await district_index.ensure(store, session, cfg.filter.city)
        if not geometries:
            result.errors["districts"] = "полигоны районов недоступны"
    else:
        geometries = district_index.cached(store, cfg.filter.city) or {}
    if not geometries:
        return
    for item in result.listings:
        item.district = district_index.locate(geometries, item.lat, item.lon)
    if cfg.filter.districts:
        result.listings = [
            item for item in result.listings if cfg.filter.matches_district(item.district)
        ]
