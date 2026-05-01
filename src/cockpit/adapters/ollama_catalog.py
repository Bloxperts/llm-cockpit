"""Ollama catalog adapter.

The local Ollama daemon does not expose a catalog-search endpoint. This
adapter reads Ollama's public model search page and turns the server-rendered
model cards into the small shape the dashboard needs.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx

OLLAMA_CATALOG_BASE_URL = "https://ollama.com"
OLLAMA_CATALOG_TIMEOUT_S = 6.0


class OllamaCatalogUnavailable(Exception):
    pass


class _OllamaCatalogParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v for k, v in attrs}
        if tag == "li" and "x-test-model" in attr:
            self._current = {
                "name": "",
                "description": "",
                "sizes": [],
                "capabilities": [],
                "pulls": None,
                "tags": None,
                "updated": None,
                "url": None,
            }
            self._field = None
            return
        if self._current is None:
            return
        if tag == "a" and self._current.get("url") is None and attr.get("href"):
            self._current["url"] = urljoin(OLLAMA_CATALOG_BASE_URL, attr["href"] or "")
        if tag == "span" and "x-test-search-response-title" in attr:
            self._field = "name"
        elif tag == "p" and self._current.get("description") == "":
            self._field = "description"
        elif tag == "span" and "x-test-size" in attr:
            self._field = "size"
        elif tag == "span" and "x-test-capability" in attr:
            self._field = "capability"
        elif tag == "span" and "x-test-pull-count" in attr:
            self._field = "pulls"
        elif tag == "span" and "x-test-tag-count" in attr:
            self._field = "tags"
        elif tag == "span" and "x-test-updated" in attr:
            self._field = "updated"

    def handle_data(self, data: str) -> None:
        if self._current is None or self._field is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._field == "size":
            self._current["sizes"].append(text)
        elif self._field == "capability":
            self._current["capabilities"].append(text)
        else:
            existing = self._current.get(self._field)
            self._current[self._field] = f"{existing} {text}".strip() if existing else text

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag in {"span", "p"}:
            self._field = None
        if tag == "li":
            if self._current.get("name"):
                self.items.append(self._current)
            self._current = None
            self._field = None


def parse_ollama_catalog(html: str, *, installed: set[str], limit: int) -> list[dict[str, Any]]:
    parser = _OllamaCatalogParser()
    parser.feed(html)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parser.items:
        name = str(item.get("name") or "").strip()
        base = name.split(":", 1)[0]
        if not name or name in installed or base in installed or name in seen:
            continue
        item["name"] = name
        rows.append(item)
        seen.add(name)
        if len(rows) >= limit:
            break
    return rows


async def search_ollama_catalog(
    *,
    query: str,
    installed: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    url = f"{OLLAMA_CATALOG_BASE_URL}/search"
    if query:
        url = f"{url}?q={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_CATALOG_TIMEOUT_S, follow_redirects=True) as client:
            response = await client.get(url, headers={"Accept": "text/html"})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaCatalogUnavailable(str(exc)) from exc
    return parse_ollama_catalog(response.text, installed=installed, limit=limit)
