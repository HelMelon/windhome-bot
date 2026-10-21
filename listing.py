"""Общая модель объявления, к которой приводятся все источники."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    """Нормализованная карточка: рендер не знает, откуда она пришла."""

    source: str
    uid: str
    title: str
    url: str
    price_byn: int | None = None
    price_note: str | None = None
    address: str | None = None
    area_total: float | None = None
    area_living: float | None = None
    rooms: int | None = None
    floor: int | None = None
    floors: int | None = None
    seller: str | None = None
    district: str | None = None
    lat: float | None = None
    lon: float | None = None
    extra: list[str] = field(default_factory=list)

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None
