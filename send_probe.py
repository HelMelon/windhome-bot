"""Проверка доставки: отправляет в чат одно сообщение и печатает message_id.

    WINDHOME_TOKEN=... venv/bin/python send_probe.py

Нужен только для проверки после установки — в обычной работе не запускается.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import Config
from net import make_session
from render import render
from sources import fetch_all
from store import Store


async def main() -> None:
    cfg = Config.load()
    store = Store(cfg.database)
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    async with make_session() as session:
        result = await fetch_all(cfg, store, session, only={"realt": True})
    price = f"{cfg.filter.price_max_byn:,}".replace(",", " ")
    rooms = ", ".join(map(str, cfg.filter.rooms)) or "любые"
    headline = (
        "✅ Windhome на связи.\n"
        f"Фильтр: {cfg.filter.city}, от {cfg.filter.area_min:g} м², "
        f"до {price} BYN, комнат: {rooms}"
    )
    await bot.send_message(cfg.chat_ids[0], headline)
    sent = None
    if result.listings:
        sent = await bot.send_message(cfg.chat_ids[0], render(result.listings[0]))
    print("ok | chat:", cfg.chat_ids[0], "| listing message_id:", sent.message_id if sent else None)
    await bot.session.close()
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
