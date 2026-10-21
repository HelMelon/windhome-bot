"""Самопроверка без Telegram: тянет все источники и печатает то, что уйдёт
в чат. Запускается вручную, чтобы убедиться, что парсеры живы.

    WINDHOME_TOKEN=x venv/bin/python selftest.py
"""

from __future__ import annotations

import asyncio

from config import Config
from net import make_session
from render import render
from sources import fetch_all
from store import Store


async def main() -> None:
    cfg = Config.load()
    store = Store(cfg.database)
    async with make_session() as session:
        result = await fetch_all(
            cfg, store, session, only={"kufar": True, "realt": True, "eri2": True}
        )
    print("курсы:", result.rates)
    print("ошибки:", result.errors or "нет")
    counts = {name: sum(1 for item in result.listings if item.source == name)
              for name in ("kufar", "realt", "eri2")}
    print("найдено по источникам:", counts, "| всего", len(result.listings))
    for listing in result.listings:
        print("-" * 60)
        print(render(listing))
        print("geo:", listing.lat, listing.lon)
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
