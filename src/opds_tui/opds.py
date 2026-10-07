from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin

import feedparser
import httpx

from .models import Entry, Feed, Link

USER_AGENT = "opds-tui/0.1 (+https://github.com/anomalyco/opencode)"
TIMEOUT = 30.0


class OPDSException(Exception):
    pass


def _absolute(base: str, href: str) -> str:
    if not href:
        return href
    return urljoin(base, href)


def _parse_atom(data: bytes, url: str) -> Feed:
    parsed = feedparser.parse(data)
    feed = Feed(
        title=parsed.feed.get("title", ""),
        id=parsed.feed.get("id", ""),
        updated=parsed.feed.get("updated", ""),
        url=url,
    )
    for link in parsed.feed.get("links", []):
        feed.links.append(
            Link(
                href=_absolute(url, link.get("href", "")),
                rel=link.get("rel", ""),
                type=link.get("type", ""),
                title=link.get("title", ""),
            )
        )
    for item in parsed.entries:
        entry = Entry(
            title=item.get("title", ""),
            id=item.get("id", ""),
            summary=item.get("summary", ""),
            updated=item.get("updated", ""),
            language=item.get("language", ""),
        )
        for author in item.get("authors", []):
            name = author.get("name")
            if name:
                entry.authors.append(name)
        for link in item.get("links", []):
            entry.links.append(
                Link(
                    href=_absolute(url, link.get("href", "")),
                    rel=link.get("rel", ""),
                    type=link.get("type", ""),
                    title=link.get("title", ""),
                )
            )
        feed.entries.append(entry)
    feed.is_navigation = bool(feed.entries) and all(e.is_navigation for e in feed.entries)
    return feed


def _parse_opds2(data: dict, url: str) -> Feed:
    feed = Feed(
        title=data.get("metadata", {}).get("title", ""),
        id=data.get("metadata", {}).get("identifier", ""),
        updated=data.get("metadata", {}).get("modified", ""),
        url=url,
    )
    for link in data.get("links", []):
        feed.links.append(
            Link(
                href=_absolute(url, link.get("href", "")),
                rel=link.get("rel", ""),
                type=link.get("type", ""),
                title=link.get("title", ""),
            )
        )
    raw_entries = data.get("navigation", []) + data.get("publications", [])
    for item in raw_entries:
        metadata = item.get("metadata", {})
        entry = Entry(
            title=metadata.get("title", ""),
            id=metadata.get("identifier", ""),
            summary=metadata.get("description", ""),
            updated=metadata.get("modified", ""),
            language=metadata.get("language", ""),
        )
        author = metadata.get("author")
        if isinstance(author, str):
            entry.authors.append(author)
        elif isinstance(author, dict):
            name = author.get("name")
            if name:
                entry.authors.append(name)
        elif isinstance(author, list):
            for a in author:
                name = a.get("name") if isinstance(a, dict) else a
                if name:
                    entry.authors.append(name)
        for link in item.get("links", []):
            entry.links.append(
                Link(
                    href=_absolute(url, link.get("href", "")),
                    rel=link.get("rel", ""),
                    type=link.get("type", ""),
                    title=link.get("title", ""),
                )
            )
        feed.entries.append(entry)
    feed.is_navigation = bool(data.get("navigation")) and not data.get("publications")
    return feed


def parse_feed(data: bytes, url: str, content_type: str = "") -> Feed:
    text = data.decode("utf-8", errors="replace").strip()
    is_json = "json" in content_type or text.startswith("{")
    if is_json:
        try:
            return _parse_opds2(json.loads(text), url)
        except json.JSONDecodeError as exc:
            raise OPDSException(f"Invalid OPDS JSON at {url}: {exc}") from exc
    return _parse_atom(data, url)


class OPDSClient:
    def __init__(self, timeout: float = TIMEOUT) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OPDSClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, url: str, attempts: int = 3) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(0.4 * (attempt + 1))
            except httpx.HTTPError as exc:
                raise OPDSException(f"Failed to fetch {url}: {exc}") from exc
        raise OPDSException(f"Failed to fetch {url}: {last_error}")

    def fetch(self, url: str) -> Feed:
        response = self._get(url)
        return parse_feed(
            response.content, str(response.url), response.headers.get("content-type", "")
        )

    def _fetch_search_template(self, feed: Feed) -> str | None:
        osd_url = feed.opensearch_url
        if not osd_url:
            return None
        try:
            response = self._get(osd_url)
            root = ET.fromstring(response.content)
        except (OPDSException, ET.ParseError):
            return None
        ns = "{http://a9.com/-/spec/opensearch/1.1/}"
        fallback: str | None = None
        for url_el in root.iter(f"{ns}Url"):
            template = url_el.get("template")
            if not template or "{searchTerms}" not in template:
                continue
            if url_el.get("type") == "application/atom+xml":
                return template
            fallback = fallback or template
        return fallback

    def search(self, feed: Feed, query: str) -> Feed:
        template = feed.search_template or self._fetch_search_template(feed)
        if template:
            url = template.replace("{searchTerms}", quote(query))
            try:
                return self.fetch(url)
            except OPDSException:
                pass
        return filter_feed(feed, query)


def filter_feed(feed: Feed, query: str) -> Feed:
    q = query.lower()

    def matches(entry: Entry) -> bool:
        haystack = " ".join([entry.title, entry.author_str, entry.summary, entry.id]).lower()
        return q in haystack

    return Feed(
        title=f"{feed.title} — search: {query}",
        id=feed.id,
        updated=feed.updated,
        url=feed.url,
        entries=[e for e in feed.entries if matches(e)],
        links=list(feed.links),
        is_navigation=False,
    )
