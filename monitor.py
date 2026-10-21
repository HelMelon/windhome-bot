"""Фоновый монитор: раз в N минут обходит источники и присылает только то,
чего ещё не было в базе. Первый запуск на пустой базе ничего не рассылает —
иначе в чат прилетит вся текущая выдача и бот будет заглушён.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import aiohttp
from aiogram import Bot

from config import Config
from listing import Listing
from net import make_session
from render import render
from sources import eri2, fetch_all
from store import Store
import userfilter

log = logging.getLogger("windhome.monitor")


@dataclass
class CycleReport:
    new: list[Listing] = field(default_factory=list)
    known: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    seeded: bool = False
    when: int = field(default_factory=lambda: int(time.time()))


class Monitor:
    def __init__(self, bot: Bot, store: Store) -> None:
        self._bot = bot
        self._store = store
        self._wake = asyncio.Event()

    async def run(self) -> None:
        while True:
            cfg = userfilter.load(self._store)
            try:
                await self.cycle(cfg, notify=True)
            except Exception:
                log.exception("Цикл монитора упал")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=cfg.poll_interval_min * 60
                )
                self._wake.clear()
            except asyncio.TimeoutError:
                continue

    def wake(self) -> None:
        self._wake.set()

    def _due_sources(self, cfg: Config) -> dict[str, bool]:
        """У источника свой интервал: ЕРИС дёргаем редко, иначе 429 по IP."""
        raw = self._store.get("source_checks")
        last: dict[str, float] = json.loads(raw) if raw else {}
        now = time.time()
        due: dict[str, bool] = {}
        for name, enabled in cfg.sources.items():
            if not enabled:
                due[name] = False
                continue
            settings = getattr(cfg, name, {}) or {}
            interval_min = float(settings.get("interval_min", cfg.poll_interval_min))
            if interval_min <= 0:
                due[name] = False  # 0 = только по кнопке, фоном не ходим
                continue
            due[name] = now - last.get(name, 0) >= interval_min * 60
        return due

    def _mark_checked(self, due: dict[str, bool], errors: dict[str, str]) -> None:
        raw = self._store.get("source_checks")
        last: dict[str, float] = json.loads(raw) if raw else {}
        now = time.time()
        for name, was_due in due.items():
            if was_due:
                last[name] = now
        for name, error in errors.items():
            if "429" in error:
                last[name] = now + 1800  # штрафная пауза, если источник троттлит
        self._store.put("source_checks", json.dumps(last))

    async def cycle(self, cfg: Config, notify: bool = True) -> CycleReport:
        paused = self._store.get("paused") == "1"
        report = CycleReport()
        due = self._due_sources(cfg)
        async with make_session() as session:
            result = await fetch_all(cfg, self._store, session, only=due)
        self._mark_checked(due, result.errors)
        report.errors = result.errors
        seen_by_source = {
            name: self._store.known_uids(name) for name in ("kufar", "realt", "eri2")
        }
        batch_uids: set[tuple[str, str]] = set()
        for listing in sorted(result.listings, key=lambda item: item.price_byn or 0):
            key = (listing.source, listing.uid)
            if listing.uid in seen_by_source.get(listing.source, set()):
                report.known += 1
                continue
            if key in batch_uids:
                continue
            batch_uids.add(key)
            report.new.append(listing)

        version = self._store.get(userfilter.VERSION_KEY) or "0"
        first_run = self._store.get("seeded") != "1"
        filter_changed = (
            not first_run and self._store.get("seen_filter_version") != version
        )
        silent = first_run or filter_changed

        if not silent:
            await self._enrich(cfg, report.new)
        for listing in report.new:
            self._store.mark_seen(
                listing.source,
                listing.uid,
                listing.price_byn,
                listing.title,
                listing.url,
            )
        self._store.put("seen_filter_version", version)
        self._store.put("seeded", "1")

        if silent:
            report.seeded = True
            reason = "Первый обход" if first_run else "Фильтр изменён"
            log.info("%s: %d объявлений приняты как известные", reason, len(report.new))
        elif notify and report.new and not paused:
            await self.notify(cfg, report.new)
        self._store.put(
            "last_cycle",
            json.dumps({"ts": report.when, "new": len(report.new),
                        "errors": report.errors}, ensure_ascii=False),
        )
        log.info(
            "цикл: источники=%s найдено=%d новых=%d известных=%d ошибки=%s",
            ",".join(sorted(name for name, due in due.items() if due)) or "нет",
            len(result.listings),
            len(report.new),
            report.known,
            report.errors or "нет",
        )
        return report

    async def _enrich(self, cfg: Config, listings: list[Listing]) -> None:
        """ЕРИС отдаёт детали отдельным запросом — тянем их только для новых."""
        pending = [item for item in listings if item.source == "eri2"]
        if not pending:
            return
        async with make_session() as session:
            await eri2.enrich_many(session, cfg, pending)

    def _recipients(self, cfg: Config) -> list[int]:
        """Личный чат из конфига + группы, разрешённые командой /allow."""
        raw = self._store.get("extra_chats")
        extra = {int(item) for item in json.loads(raw)} if raw else set()
        return sorted(set(cfg.chat_ids) | extra)

    async def notify(self, cfg: Config, listings: list[Listing]) -> None:
        """Пушит только те источники, где notify != false (ЕРИС — по кнопке)."""
        wanted = [
            item
            for item in listings
            if (getattr(cfg, item.source, {}) or {}).get("notify", True)
        ]
        if not wanted:
            return
        batch = wanted[: cfg.notify_limit]
        for chat_id in self._recipients(cfg):
            if len(batch) > 1:
                await self._bot.send_message(
                    chat_id, f"🆕 Новых объявлений: {len(batch)}"
                )
            for listing in batch:
                await self._send_listing(chat_id, listing)
            if len(listings) > len(batch):
                await self._bot.send_message(
                    chat_id,
                    f"…и ещё {len(listings) - len(batch)}. "
                    "Полный список — кнопкой в боте.",
                )

    async def _send_listing(self, chat_id: int, listing: Listing) -> None:
        try:
            await self._bot.send_message(
                chat_id,
                render(listing),
                disable_web_page_preview=False,
            )
            if listing.has_location:
                await self._bot.send_location(chat_id, listing.lat, listing.lon)
        except Exception:
            log.exception("Не смог отправить %s/%s", listing.source, listing.uid)
