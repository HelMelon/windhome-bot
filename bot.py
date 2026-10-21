"""Команды и кнопки бота. Логика выборки та же, что у монитора — handlers
лишь просят источники напрямую и печатают результат.
"""

from __future__ import annotations

import json
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config import Config
from monitor import Monitor
from net import make_session
from render import SOURCE_LABEL, render
from sources import fetch_all
from sources import eri2
from store import Store
import districts
import userfilter

log = logging.getLogger("windhome.bot")

KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🏠 Куфар"),
            KeyboardButton(text="🏡 Realt"),
            KeyboardButton(text="🏚 Заброшки"),
        ],
        [KeyboardButton(text="⚙️ Фильтр"), KeyboardButton(text="ℹ️ Статус")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Что ищем?",
)

BUTTON_TO_SOURCE = {
    "куфар": "kufar",
    "kufar": "kufar",
    "realt": "realt",
    "заброшки": "eri2",
    "ерис": "eri2",
}

AREA_PRESETS = (50, 60, 70, 80, 100)
PRICE_PRESETS = (150_000, 200_000, 250_000, 300_000, 350_000)
ROOM_PRESETS = (("1", "1"), ("2", "2"), ("3", "3"), ("3,4", "3,4"), ("любые", "any"))


def _filter_summary(cfg: Config) -> str:
    rooms = ", ".join(str(room) for room in cfg.filter.rooms) or "любые"
    return (
        f"{cfg.filter.city} · площадь от {cfg.filter.area_min:g} м² · "
        f"цена до {cfg.filter.price_max_byn:,} BYN · комнат: {rooms}"
    ).replace(",", " ")


def allowed_chats(cfg: Config, store: Store) -> set[int]:
    """Чаты из config.json плюс те, что разрешили командой /allow (группы)."""
    raw = store.get("extra_chats")
    extra = {int(item) for item in json.loads(raw)} if raw else set()
    return set(cfg.chat_ids) | extra


def is_owner(cfg: Config, user_id: int | None) -> bool:
    """Владелец — тот, чей личный chat_id прописан в конфиге."""
    return user_id is not None and user_id in cfg.chat_ids


def _command_of(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("/"):
        return ""
    return text.split()[0].split("@")[0].lower()


def _filter_keyboard(
    cfg: Config,
    store: Store,
    count: int | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки под сообщением-панелью фильтра; текущее значение помечено ✓."""
    current = cfg.filter

    def mark(active: bool, label: str) -> str:
        return f"✓ {label}" if active else label

    def rooms_active(preset: str) -> bool:
        if preset == "any":
            return not current.rooms
        return tuple(current.rooms) == tuple(int(part) for part in preset.split(","))

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=mark(current.area_min == area, f"{area}+ м²"),
                callback_data=f"f:a:{area}",
            )
            for area in AREA_PRESETS
        ],
        [
            InlineKeyboardButton(
                text=mark(current.price_max_byn == price, f"{price // 1000}k"),
                callback_data=f"f:p:{price}",
            )
            for price in PRICE_PRESETS
        ],
        [
            InlineKeyboardButton(
                text=mark(rooms_active(value), label),
                callback_data=f"f:r:{value}",
            )
            for label, value in ROOM_PRESETS
        ],
    ]

    district_names = districts.DEFAULT_DISTRICTS.get(current.city, ())
    if district_names:
        rows.append(
            [
                InlineKeyboardButton(
                    text=mark(
                        any(
                            wanted.lower() in name.lower()
                            for wanted in current.districts
                        ),
                        name.replace("Железнодорожный", "Ж/дорожный")[:16],
                    ),
                    callback_data=f"f:d:{index}",
                )
                for index, name in enumerate(district_names)
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=mark(not current.districts, "Район: любой"),
                    callback_data="f:d:any",
                )
            ]
        )

    if count:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👀 Показать {count}", callback_data="f:show"
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="♻️ Сбросить", callback_data="f:reset"),
            InlineKeyboardButton(text="🔄 Пересчитать", callback_data="f:refresh"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _by_line(by: dict[str, int]) -> str:
    return ", ".join(
        f"{SOURCE_LABEL.get(name, name)}: {value}" for name, value in by.items()
    )


async def preview(cfg: Config, store: Store) -> tuple[list, dict[str, int], str | None]:
    """Живая проверка выдачи Kufar+Realt без отметки «уже видели»."""
    only = {
        "kufar": True,
        "realt": bool(cfg.sources.get("realt", True)),
        "eri2": False,
    }
    async with make_session() as session:
        result = await fetch_all(cfg, store, session, only=only)
    by: dict[str, int] = {}
    for item in result.listings:
        by[item.source] = by.get(item.source, 0) + 1
    error = next(iter(result.errors.values()), None)
    return result.listings, by, error


def panel_text(
    cfg: Config,
    store: Store,
    items: list | None,
    by: dict[str, int],
    error: str | None,
    prefix: str | None = None,
) -> str:
    text = userfilter.describe(cfg, store)
    if error:
        text += f"\n\n⚠️ проверка не удалась: {error}"
    elif items is None:
        text += "\n\nСчитаю подходящие объявления…"
    elif not items:
        text += (
            "\n\nПод фильтр сейчас: ничего не подходит.\n"
            "Ослабь площадь, цену или убери район — и нажми «Пересчитать»."
        )
    else:
        text += f"\n\nПод фильтр сейчас: {len(items)} ({_by_line(by)})"
    return f"{prefix}\n\n{text}" if prefix else text


def build_router(bot: Bot, store: Store, monitor: Monitor) -> Router:
    router = Router()

    @router.message.middleware()
    async def gate(handler, event: Message, data):
        """Личный бот: в чужих группах молчим, в чужой личке подсказываем chat_id."""
        cfg = userfilter.load(store)
        if event.chat.id in allowed_chats(cfg, store):
            return await handler(event, data)
        if _command_of(event.text) in {"/id", "/allow", "/start", "/help"}:
            return await handler(event, data)
        if event.chat.type == "private":
            await event.answer(
                f"Это личный бот. Твой chat_id: <code>{event.chat.id}</code>"
            )
        return None

    @router.message(Command("id"))
    async def cmd_id(message: Message) -> None:
        await message.answer(f"chat_id: <code>{message.chat.id}</code>")

    @router.message(Command("allow"))
    async def cmd_allow(message: Message) -> None:
        cfg = userfilter.load(store)
        author = message.from_user.id if message.from_user else None
        if not is_owner(cfg, author):
            await message.answer("Только для владельца бота.")
            return
        chat_id = message.chat.id
        raw = store.get("extra_chats")
        extra = {int(item) for item in json.loads(raw)} if raw else set()
        extra.add(chat_id)
        store.put("extra_chats", json.dumps(sorted(extra)))
        await message.answer(
            f"Чат <code>{chat_id}</code> добавлен: здесь работают и команды, "
            "и уведомления о новых объявлениях."
        )

    @router.message(Command("forget"))
    async def cmd_forget(message: Message) -> None:
        cfg = userfilter.load(store)
        author = message.from_user.id if message.from_user else None
        if not is_owner(cfg, author):
            await message.answer("Только для владельца бота.")
            return
        raw = store.get("extra_chats")
        extra = {int(item) for item in json.loads(raw)} if raw else set()
        extra.discard(message.chat.id)
        store.put("extra_chats", json.dumps(sorted(extra)))
        await message.answer("Чат убран из списка получателей.")

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        cfg = userfilter.load(store)
        await message.answer(
            "Слежу за объявлениями и присылаю только новые.\n\n"
            f"Фильтр: {_filter_summary(cfg)}\n"
            f"Проверка каждые {cfg.poll_interval_min} мин.\n\n"
            "В личке — кнопки ниже (полная выдача на текущий момент).\n"
            "В группе: /allow — включить бота в этом чате, дальше команды "
            "/kufar, /realt, /zabroshki.\n"
            "/status — состояние, /pause и /resume — уведомления.",
            reply_markup=KEYBOARD if message.chat.type == "private" else None,
        )

    @router.message(Command("status"))
    @router.message(F.text.lower().contains("статус"))
    async def cmd_status(message: Message) -> None:
        cfg = userfilter.load(store)
        last = store.get("last_cycle")
        lines = [
            f"Фильтр: {_filter_summary(cfg)}",
            f"В базе: Kufar {store.count('kufar')} · Realt {store.count('realt')} · "
            f"ЕРИС {store.count('eri2')}",
            f"Уведомления: {'на паузе' if store.get('paused') == '1' else 'включены'}",
        ]
        if last:
            payload = json.loads(last)
            age = int((time.time() - payload["ts"]) / 60)
            lines.append(f"Последняя проверка: {age} мин назад, новых {payload['new']}")
            if payload.get("errors"):
                for name, error in payload["errors"].items():
                    lines.append(f"⚠️ {SOURCE_LABEL.get(name, name)}: {error}")
        await message.answer("\n".join(lines))

    async def safe_edit(
        message: Message,
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        """Повторное нажатие на активную кнопку не должно ронять handler."""
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                raise

    async def show_panel(
        target: Message,
        prefix: str | None = None,
        edit: bool = False,
    ) -> None:
        cfg = userfilter.load(store)
        text = panel_text(cfg, store, None, {}, None, prefix)
        keyboard = _filter_keyboard(cfg, store)
        sent = (
            await target.edit_text(text, reply_markup=keyboard)
            if edit
            else await target.answer(text, reply_markup=keyboard)
        )
        items, by, error = await preview(cfg, store)
        await safe_edit(
            sent,
            panel_text(cfg, store, items, by, error, prefix),
            _filter_keyboard(cfg, store, len(items) if items else 0),
        )

    @router.message(Command("filter", "фильтр"))
    async def cmd_filter(message: Message) -> None:
        args = (message.text or "").split()[1:]
        current = userfilter.load(store)
        if not args:
            await show_panel(message)
            return
        key = args[0].lower()
        if key in {"reset", "сброс"}:
            userfilter.reset(store, actor=f"chat:{message.chat.id}")
            await show_panel(message, "Вернул значения из config.json.")
            return
        target = userfilter.ALIASES.get(key)
        value = " ".join(args[1:]).strip()
        if target is None or not value:
            await message.answer(userfilter.HELP)
            return
        try:
            report = userfilter.set_value(
                current, store, target, value, actor=f"chat:{message.chat.id}"
            )
        except ValueError as error:
            await message.answer(f"⚠️ {error}\n\n{userfilter.HELP}")
            return
        await show_panel(message, report)

    async def toast(query: CallbackQuery, text: str, alert: bool = False) -> None:
        """Ответ на нажатие; устаревший query_id не должен ломать обработку."""
        try:
            await query.answer(text, show_alert=alert)
        except Exception:
            log.debug("ответ на callback не прошёл: %s", text)

    @router.callback_query(F.data.startswith("f:"))
    async def on_filter_button(query: CallbackQuery) -> None:
        cfg = userfilter.load(store)
        chat_id = query.message.chat.id if query.message else None
        if chat_id is not None and chat_id not in allowed_chats(cfg, store):
            await toast(query, "Это личный бот.")
            return
        _, action, *rest = query.data.split(":")
        value = rest[0] if rest else ""
        if action == "show":
            await toast(query, "Собираю выдачу…")
            items, by, error = await preview(cfg, store)
            if error:
                await query.message.answer(f"⚠️ {error}")
                return
            if not items:
                await query.message.answer("Под фильтр сейчас ничего не подходит.")
                return
            shown = sorted(items, key=lambda item: item.price_byn or 0)[
                : cfg.notify_limit
            ]
            for item in shown:
                await query.message.answer(render(item))
                if item.has_location:
                    await query.message.answer_location(item.lat, item.lon)
                store.mark_seen(
                    item.source, item.uid, item.price_byn, item.title, item.url
                )
            if len(items) > len(shown):
                await query.message.answer(
                    f"…и ещё {len(items) - len(shown)} — сузь фильтр "
                    "или открой источник отдельной кнопкой."
                )
            return
        if action in {"check", "refresh"}:
            items, by, error = await preview(cfg, store)
            status = (
                f"⚠️ {error}"
                if error
                else (f"Подходит: {len(items)} ({_by_line(by)})" if items
                      else "Ничего не подходит")
            )
            await toast(query, status)
            if query.message is not None:
                await safe_edit(
                    query.message,
                    panel_text(cfg, store, items, by, error),
                    _filter_keyboard(cfg, store, len(items) if items else 0),
                )
            return
        if action == "reset":
            userfilter.reset(store, actor=f"chat:{chat_id}")
            notice = "Фильтр сброшен к config.json"
        else:
            key = {"a": "area_min", "p": "price_max_byn", "r": "rooms", "d": "districts"}.get(action)
            if key is None:
                await toast(query, "Неизвестная кнопка")
                return
            if key == "districts":
                names = districts.DEFAULT_DISTRICTS.get(cfg.filter.city, ())
                if value == "any":
                    value = "любой"
                elif value.isdigit() and int(value) < len(names):
                    value = names[int(value)]
            try:
                notice = userfilter.set_value(
                    cfg, store, key, value, actor=f"chat:{chat_id}"
                )
            except ValueError as error:
                await toast(query, f"⚠️ {error}", alert=True)
                return
        fresh = userfilter.load(store)
        items, by, error = await preview(fresh, store)
        await toast(
            query,
            notice if error else (
                f"{notice}. Подходит: {len(items)}" if items
                else f"{notice}. Под фильтр сейчас ничего"
            ),
        )
        if query.message is not None:
            await safe_edit(
                query.message,
                panel_text(fresh, store, items, by, error),
                _filter_keyboard(fresh, store, len(items) if items else 0),
            )

    @router.message(Command("pause"))
    async def cmd_pause(message: Message) -> None:
        store.put("paused", "1")
        await message.answer("Уведомления на паузе. /resume — включить обратно.")

    @router.message(Command("resume"))
    async def cmd_resume(message: Message) -> None:
        store.put("paused", "0")
        await message.answer("Уведомления включены.")

    @router.message(Command("check"))
    async def cmd_check(message: Message) -> None:
        await message.answer("Проверяю источники…")
        cfg = userfilter.load(store)
        items, by, error = await preview(cfg, store)
        if error:
            await message.answer(f"⚠️ {error}")
            return
        if not items:
            await message.answer(
                "Под фильтр сейчас ничего не подходит. Ослабь условия — "
                "/filter покажет панель с кнопками."
            )
            return
        await message.answer(
            f"Под фильтр сейчас: {len(items)} ({_by_line(by)}).\n"
            "Показать — «👀 Показать» в панели /filter."
        )

    async def send_source(message: Message, source: str) -> None:
        cfg = userfilter.load(store)
        only = {name: name == source for name in cfg.sources}
        await message.answer(f"Смотрю {SOURCE_LABEL.get(source, source)}…")
        async with make_session() as session:
            result = await fetch_all(cfg, store, session, only=only)
            if source == "eri2" and result.listings:
                result.listings = await eri2.enrich_many(
                    session, cfg, result.listings
                )
        if result.errors:
            error = next(iter(result.errors.values()))
            await message.answer(f"⚠️ {SOURCE_LABEL.get(source, source)}: {error}")
            return
        if not result.listings:
            await message.answer("Под фильтр сейчас ничего не подходит.")
            return
        listings = sorted(result.listings, key=lambda item: item.price_byn or 0)
        await message.answer(f"Найдено {len(listings)}, показываю "
                             f"{min(len(listings), cfg.notify_limit)}:")
        for listing in listings[: cfg.notify_limit]:
            await message.answer(render(listing))
            if listing.has_location:
                await message.answer_location(listing.lat, listing.lon)

    @router.message(Command("kufar"))
    async def cmd_kufar(message: Message) -> None:
        await send_source(message, "kufar")

    @router.message(Command("realt"))
    async def cmd_realt(message: Message) -> None:
        await send_source(message, "realt")

    @router.message(Command("zabroshki", "eri2"))
    async def cmd_eri2(message: Message) -> None:
        await send_source(message, "eri2")

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        text = (message.text or "").strip().lower()
        if "фильтр" in text:
            await show_panel(message)
            return
        for button, source in BUTTON_TO_SOURCE.items():
            if button in text:
                await send_source(message, source)
                return
        if message.chat.type == "private":
            await message.answer(
                "Не понял. Кнопки внизу или /status.", reply_markup=KEYBOARD
            )

    return router
