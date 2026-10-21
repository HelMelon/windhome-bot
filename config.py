"""Загрузка настроек: config.json рядом с кодом, секреты — из окружения.

Конфиг вынесен в JSON, чтобы менять фильтры без правки кода и без рестарта
(перечитывается на каждом цикле монитора).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("WINDHOME_CONFIG", BASE_DIR / "config.json"))


@dataclass(frozen=True)
class Filter:
    """Общие для всех источников условия поиска."""

    city: str
    region_contains: str
    area_min: float
    price_max_byn: int
    rooms: tuple[int, ...]
    districts: tuple[str, ...] = ()

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Filter":
        return Filter(
            city=raw.get("city", "Гомель"),
            region_contains=raw.get("region_contains", "Гомельская обл"),
            area_min=float(raw.get("area_min", 0)),
            price_max_byn=int(raw.get("price_max_byn", 0)),
            rooms=tuple(raw.get("rooms") or ()),
            districts=tuple(raw.get("districts") or ()),
        )

    def matches_price(self, price_byn: int | None) -> bool:
        return price_byn is None or price_byn <= self.price_max_byn

    def matches_area(self, area: float | None) -> bool:
        return area is not None and area >= self.area_min

    def matches_rooms(self, rooms: int | None) -> bool:
        return not self.rooms or (rooms is not None and rooms in self.rooms)

    def matches_district(self, district: str | None) -> bool:
        """Район не определился (нет координат) — объявление не выбрасываем."""
        if not self.districts:
            return True
        if district is None:
            return True
        low = district.lower()
        return any(wanted.lower() in low for wanted in self.districts)


@dataclass(frozen=True)
class Config:
    token: str
    chat_ids: tuple[int, ...]
    poll_interval_min: int
    notify_limit: int
    database: Path
    filter: Filter
    sources: dict[str, bool]
    kufar: dict[str, Any]
    realt: dict[str, Any]
    eri2: dict[str, Any]

    @staticmethod
    def load() -> "Config":
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        token = os.environ.get("WINDHOME_TOKEN") or raw.get("token", "")
        if not token:
            raise SystemExit("Нет токена: задайте WINDHOME_TOKEN или token в config.json")
        return Config(
            token=token,
            chat_ids=tuple(int(c) for c in raw.get("chat_ids", [])),
            poll_interval_min=int(raw.get("poll_interval_min", 20)),
            notify_limit=int(raw.get("notify_limit", 15)),
            database=(BASE_DIR / raw.get("database", "data/windhome.db")).resolve(),
            filter=Filter.from_dict(raw.get("filter", {})),
            sources=raw.get("sources", {}),
            kufar=raw.get("kufar", {}),
            realt=raw.get("realt", {}),
            eri2=raw.get("eri2", {}),
        )
