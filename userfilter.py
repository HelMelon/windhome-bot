"""Правка фильтра из бота: значения из чата ложатся поверх config.json.

Хранятся в SQLite (kv `filter_override`), а не в файле: правки из Telegram не
должны конфликтовать с деплоем, а config.json остаётся значением по умолчанию
и точкой сброса. Любое изменение поднимает `filter_version` — по нему монитор
понимает, что выдачу после смены фильтра надо принять молча, иначе расширение
фильтра выливается в лавину «новых» старых объявлений.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, replace
from typing import Any

from config import Config, Filter
from store import Store

OVERRIDE_KEY = "filter_override"
VERSION_KEY = "filter_version"
HISTORY_KEY = "filter_history"
NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

ALIASES = {
    "площадь": "area_min",
    "площади": "area_min",
    "area": "area_min",
    "цена": "price_max_byn",
    "цену": "price_max_byn",
    "price": "price_max_byn",
    "комнаты": "rooms",
    "комнат": "rooms",
    "rooms": "rooms",
    "город": "city",
    "city": "city",
    "район": "districts",
    "районы": "districts",
    "р-н": "districts",
    "district": "districts",
    "districts": "districts",
    "регион": "region_contains",
    "region": "region_contains",
}

HELP = (
    "Как менять фильтр:\n"
    "/filter — показать текущий\n"
    "/filter площадь от 60 — минимальная общая площадь, м²\n"
    "/filter цена до 300000 — потолок цены в BYN\n"
    "/filter комнаты 3,4 — список комнат (пусто или «любые» — без ограничения)\n"
    "/filter район Центральный — район города (можно несколько: «Советский,Центральный»)\n"
    "/filter город Гомель\n"
    "/filter регион Гомельская — только для ЕРИС; «любой» = все области\n"
    "/filter reset — вернуть значения из config.json\n\n"
    "Коротко можно по одному слову: `/filter цена 300000`, `/filter площадь 60`, "
    "`/filter комнаты 3`."
)


def overrides(store: Store) -> dict[str, Any]:
    raw = store.get(OVERRIDE_KEY)
    return json.loads(raw) if raw else {}


def describe_overrides(store: Store) -> set[str]:
    return set(overrides(store))


def effective(cfg: Config, store: Store) -> Config:
    """Config с наложенными правками из чата."""
    patch = overrides(store)
    if not patch:
        return cfg
    merged = {**asdict(cfg.filter), **patch}
    for key in ("rooms", "districts"):
        if isinstance(merged.get(key), list):
            merged[key] = tuple(merged[key])
    return replace(cfg, filter=Filter(**merged))


def load(store: Store) -> Config:
    return effective(Config.load(), store)


def _parse_number(raw: str) -> float:
    """Достаёт первое число из строки: «до 300 000», «60 м²», «2,5»."""
    text = raw.replace(" ", "").replace("\u00a0", "")
    match = NUMBER_RE.search(text)
    if not match:
        raise ValueError(f"«{raw}» — не число")
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError as error:
        raise ValueError(f"«{raw}» — не число") from error


def _parse_rooms(raw: str) -> list[int]:
    text = raw.strip().lower()
    if not text or text in {"любые", "any", "-", "0", "все"}:
        return []
    rooms: list[int] = []
    for chunk in text.replace(" ", "").split(","):
        if not chunk:
            continue
        if not chunk.isdigit():
            raise ValueError(f"«{chunk}» — не число комнат")
        rooms.append(int(chunk))
    return sorted(set(rooms))


def _parse_districts(raw: str) -> list[str]:
    """«Советский, Центральный район» -> ('Советский', 'Центральный')."""
    text = raw.strip()
    if not text or text.lower() in {"любой", "любые", "все", "any", "-"}:
        return []
    names: list[str] = []
    for chunk in text.split(","):
        name = chunk.strip()
        for tail in ("район", "р-н", "р-не"):
            if name.lower().endswith(tail):
                name = name[: -len(tail)].strip()
        if name:
            names.append(name[:1].upper() + name[1:])
    return names


def _remember(store: Store, key: str, value: Any, actor: str | None) -> None:
    """Журнал правок: без него «фильтр сам поменялся» невозможно проверить."""
    raw = store.get(HISTORY_KEY)
    history = json.loads(raw) if raw else []
    history.append(
        {"ts": int(time.time()), "key": key, "value": value, "actor": actor or "?"}
    )
    store.put(HISTORY_KEY, json.dumps(history[-20:], ensure_ascii=False))


def history(store: Store) -> list[dict[str, Any]]:
    raw = store.get(HISTORY_KEY)
    return json.loads(raw) if raw else []


def set_value(
    cfg: Config,
    store: Store,
    key: str,
    raw: str,
    actor: str | None = None,
) -> str:
    """Возвращает человекочитаемый отчёт; на кривой ввод бросает ValueError."""
    patch = overrides(store)
    if key == "area_min":
        value = _parse_number(raw)
        if value < 0 or value > 1000:
            raise ValueError("площадь должна быть от 0 до 1000 м²")
        patch[key] = value
        message = f"Площадь: от {value:g} м²"
    elif key == "price_max_byn":
        value = int(_parse_number(raw))
        if value < 1000:
            raise ValueError("цена должна быть больше 1 000 BYN")
        patch[key] = value
        message = f"Цена: до {value:,} BYN".replace(",", " ")
    elif key == "rooms":
        rooms = _parse_rooms(raw)
        patch[key] = rooms
        message = "Комнаты: " + (", ".join(map(str, rooms)) if rooms else "любые")
    elif key == "districts":
        names = _parse_districts(raw)
        patch[key] = names
        message = "Район: " + (", ".join(names) if names else "любой")
    elif key == "city":
        city = raw.strip()
        if not city:
            raise ValueError("город не может быть пустым")
        patch[key] = city
        message = f"Город: {city}"
    elif key == "region_contains":
        region = "" if raw.strip().lower() in {"любой", "все", "any", "-"} else raw.strip()
        patch[key] = region
        message = f"ЕРИС: {'все области' if not region else region}"
    else:
        raise ValueError(f"неизвестный параметр «{key}»")

    store.put(OVERRIDE_KEY, json.dumps(patch, ensure_ascii=False))
    store.put(VERSION_KEY, str(int(time.time())))
    _remember(store, key, patch.get(key), actor)
    return message


def reset(store: Store, actor: str | None = None) -> None:
    store.put(OVERRIDE_KEY, "{}")
    store.put(VERSION_KEY, str(int(time.time())))
    _remember(store, "reset", {}, actor)


def describe(cfg: Config, store: Store) -> str:
    patch = overrides(store)
    rooms = ", ".join(map(str, cfg.filter.rooms)) or "любые"
    districts = ", ".join(cfg.filter.districts) or "любой"
    price = f"{cfg.filter.price_max_byn:,}".replace(",", " ")
    region = cfg.filter.region_contains or "все области"
    mark = lambda key: "  ← из бота" if key in patch else ""
    lines = [
        f"Город: {cfg.filter.city}{mark('city')}",
        f"Площадь: от {cfg.filter.area_min:g} м²{mark('area_min')}",
        f"Цена: до {price} BYN{mark('price_max_byn')}",
        f"Комнаты: {rooms}{mark('rooms')}",
        f"Район: {districts}{mark('districts')}",
        f"ЕРИС: {region}{mark('region_contains')}",
    ]
    changes = history(store)
    if changes:
        entry = changes[-1]
        when = time.strftime("%H:%M", time.localtime(entry["ts"]))
        what = "сброс" if entry["key"] == "reset" else entry["key"]
        lines.append(f"Последняя правка: {when} — {what} ({entry['actor']})")
    lines.append("")
    lines.append(
        "Изменить: /filter площадь 60 · /filter цена 300000 · "
        "/filter комнаты 3,4 · /filter район Центральный · /filter reset"
    )
    return "\n".join(lines)
