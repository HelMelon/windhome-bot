"""Текст сообщений. Один формат на все источники, различается только шапка,
чтобы список в чате читался с одного взгляда.
"""

from __future__ import annotations

from html import escape

from listing import Listing

SOURCE_LABEL = {"kufar": "Kufar", "realt": "Realt", "eri2": "ЕРИС"}
SOURCE_ICON = {"kufar": "🏠", "realt": "🏡", "eri2": "🏚"}


def format_byn(amount: int | None) -> str:
    if amount is None:
        return "цена не указана"
    return f"{amount:,}".replace(",", " ") + " BYN"


def _area_line(listing: Listing) -> str | None:
    if listing.area_total is None:
        return None
    line = f"{listing.area_total:g} м²"
    if listing.rooms:
        line = f"{listing.rooms}-к. · {line}"
    return line


def _floor_line(listing: Listing) -> str | None:
    if listing.floor is None:
        return None
    if listing.floors:
        return f"{listing.floor}/{listing.floors} эт."
    return f"{listing.floor} эт."


def render(listing: Listing) -> str:
    icon = SOURCE_ICON.get(listing.source, "🏠")
    head = f"{icon} <b>{escape(listing.title)}</b>"
    price = format_byn(listing.price_byn)
    if listing.price_note:
        price += f" ({listing.price_note})"
    lines = [head, price]
    if listing.address and listing.address != listing.title:
        lines.append(escape(listing.address))
    details = [part for part in (_area_line(listing), _floor_line(listing)) if part]
    if listing.district:
        details.append(f"{escape(listing.district)} р-н")
    if listing.seller:
        details.append(escape(listing.seller))
    if details:
        lines.append(" · ".join(details))
    lines.extend(escape(item) for item in listing.extra)
    lines.append(f'{SOURCE_LABEL.get(listing.source, listing.source)}: '
                 f'<a href="{escape(listing.url)}">открыть</a>')
    return "\n".join(lines)
