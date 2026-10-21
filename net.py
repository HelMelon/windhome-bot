"""HTTP-сессия с браузерным User-Agent.

Без него часть площадок (и точно Realt) отдаёт урезанную выдачу, поэтому UA
задаётся один раз на сессию, а не в каждом запросе.
"""

from __future__ import annotations

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ru,en;q=0.9",
}


def make_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(headers=DEFAULT_HEADERS)
