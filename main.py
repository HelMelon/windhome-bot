"""Точка входа: один процесс = бот + фоновый монитор.

Один процесс вместо двух сервисов: монитору нужен тот же Bot, а общая
SQLite-база в WAL нормально живёт в одном соединении.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot import build_router
from config import Config
from monitor import Monitor
from store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("windhome")


async def main() -> None:
    cfg = Config.load()
    store = Store(cfg.database)
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    monitor = Monitor(bot, store)
    dispatcher.include_router(build_router(bot, store, monitor))
    log.info("Фильтр: %s", cfg.filter)
    monitor_task = asyncio.create_task(monitor.run())
    try:
        await dispatcher.start_polling(bot)
    finally:
        monitor_task.cancel()
        store.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
