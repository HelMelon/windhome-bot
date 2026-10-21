"""Курсы НБРБ: Realt отдаёт цены в USD/EUR/BYN, сравнивать с лимитом в BYN
можно только через официальный курс. Кэш на 6 часов, при недоступности
API — последний известный курс из БД, иначе значение из конфига.
"""

from __future__ import annotations

import json
import time

import aiohttp

from store import Store

NBRB_URL = "https://api.nbrb.by/exrates/rates/{code}"
CACHE_TTL_S = 6 * 3600
CURRENCY_CODES = {"USD": 431, "EUR": 451}


async def _fetch_rate(session: aiohttp.ClientSession, currency: str) -> float | None:
    url = NBRB_URL.format(code=CURRENCY_CODES[currency])
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            payload = await resp.json(content_type=None)
        return float(payload["Cur_OfficialRate"] / payload["Cur_Scale"])
    except Exception:
        return None


async def load_rates(
    session: aiohttp.ClientSession,
    store: Store,
    fallback_usd: float = 3.0,
) -> dict[str, float]:
    cached_raw = store.get("rates")
    if cached_raw:
        cached = json.loads(cached_raw)
        if time.time() - cached.get("ts", 0) < CACHE_TTL_S:
            return cached["rates"]
    rates: dict[str, float] = {}
    for currency in ("USD", "EUR"):
        rate = await _fetch_rate(session, currency)
        if rate:
            rates[currency] = rate
    if rates:
        store.put("rates", json.dumps({"ts": int(time.time()), "rates": rates}))
        return rates
    if cached_raw:
        return json.loads(cached_raw)["rates"]
    return {"USD": fallback_usd, "EUR": fallback_usd}
