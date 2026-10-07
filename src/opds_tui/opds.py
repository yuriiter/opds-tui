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


def _expand_search_template(template: str, query: str) -> str | None:
    """Expand a search URL template for an OpenSearch or RFC 6570 placeholder."""
    q = quote(query)
    if "{searchTerms}" in template:
        return template.replace("{searchTerms}", q)
    if "{?query}" in template:
        prefix = template.split("{?query}", 1)[0]
        sep = "&" if "?" in prefix else "?"
        return template.replace("{?query}", f"{sep}query={q}")
    if "{query}" in template:
        return template.replace("{query}", q)
    return None


LANG_ALIASES: dict[str, set[str]] = {
    "de": {"de", "deu", "ger", "german", "deutsch"},
    "es": {"es", "spa", "spanish", "espanol", "español"},
    "fr": {"fr", "fra", "fre", "french", "francais", "français"},
    "it": {"it", "ita", "italian", "italiano"},
    "en": {"en", "eng", "english"},
    "ru": {"ru", "rus", "russian", "русский"},
}


def entry_matches_language(entry: Entry, language: str) -> bool:
    target = language.strip().lower()
    aliases = LANG_ALIASES.get(target, {target})
    value = entry.language
    if isinstance(value, (list, tuple)):
        values = [str(v).strip().lower() for v in value]
    elif isinstance(value, str):
        values = [value.strip().lower()]
    else:
        return False
    return any(v and (v in aliases or any(v.startswith(a) for a in aliases)) for v in values)


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


def _link_from(link: dict, url: str) -> Link:
    return Link(
        href=_absolute(url, link.get("href", "")),
        rel=link.get("rel", ""),
        type=link.get("type", ""),
        title=link.get("title", ""),
    )


def _coerce_language(value: object) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return value if isinstance(value, str) else ""


def _entry_from_publication(item: dict, url: str) -> Entry:
    metadata = item.get("metadata", {})
    entry = Entry(
        title=metadata.get("title", ""),
        id=metadata.get("identifier", ""),
        summary=metadata.get("description", ""),
        updated=metadata.get("modified", ""),
        language=_coerce_language(metadata.get("language", "")),
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
        entry.links.append(_link_from(link, url))
    return entry


def _entry_from_navigation(item: dict, url: str) -> Entry:
    metadata = item.get("metadata", {})
    href = item.get("href", "")
    title = item.get("title") or metadata.get("title", "")
    entry = Entry(
        title=title,
        id=item.get("identifier") or href,
        summary=metadata.get("description", ""),
        updated=metadata.get("modified", ""),
        language=_coerce_language(metadata.get("language", "")),
    )
    if href:
        entry.links.append(
            Link(
                href=_absolute(url, href),
                rel=item.get("rel", "subsection"),
                type=item.get("type", ""),
                title=title,
            )
        )
    for link in item.get("links", []):
        entry.links.append(_link_from(link, url))
    return entry


def _parse_opds2(data: dict, url: str) -> Feed:
    feed = Feed(
        title=data.get("metadata", {}).get("title", ""),
        id=data.get("metadata", {}).get("identifier", ""),
        updated=data.get("metadata", {}).get("modified", ""),
        url=url,
    )
    for link in data.get("links", []):
        feed.links.append(_link_from(link, url))

    for item in data.get("navigation", []):
        feed.entries.append(_entry_from_navigation(item, url))

    for item in data.get("publications", []):
        feed.entries.append(_entry_from_publication(item, url))

    # OPDS 2 groups (e.g. Internet Archive) hold publications plus a nav link
    for group in data.get("groups", []):
        metadata = group.get("metadata", {})
        group_links = group.get("links", [])
        if group_links and group_links[0].get("href"):
            feed.entries.append(
                _entry_from_navigation(
                    {
                        "title": metadata.get("title", ""),
                        "href": group_links[0]["href"],
                        "rel": group_links[0].get("rel", "subsection"),
                        "type": group_links[0].get("type", ""),
                    },
                    url,
                )
            )
        for item in group.get("publications", []):
            feed.entries.append(_entry_from_publication(item, url))

    feed.is_navigation = bool(data.get("navigation")) and not (
        data.get("publications") or data.get("groups")
    )
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

    def _fetch_pages(self, feed: Feed, max_pages: int) -> Feed:
        pages = 1
        while pages < max_pages and feed.next_url:
            try:
                nxt = self.fetch(feed.next_url)
            except OPDSException:
                break
            feed.entries.extend(nxt.entries)
            feed.links = nxt.links
            pages += 1
        return feed

    def search(self, feed: Feed, query: str, max_pages: int = 1) -> Feed:
        template = feed.search_template or self._fetch_search_template(feed)
        if template:
            url = _expand_search_template(template, query)
            if url:
                try:
                    return self._fetch_pages(self.fetch(url), max_pages)
                except OPDSException:
                    pass
        return filter_feed(feed, query)

    def search_all(
        self,
        catalogs: list,
        query: str,
        language: str | None = None,
        max_pages: int = 1,
    ) -> tuple[list[tuple[str, Entry]], dict[str, str]]:
        results: list[tuple[str, Entry]] = []
        errors: dict[str, str] = {}
        for catalog in catalogs:
            try:
                feed = self.fetch(catalog.url)
                found = self.search(feed, query, max_pages=max_pages)
            except OPDSException as exc:
                errors[catalog.name] = str(exc)
                continue
            for entry in found.entries:
                if not entry.best_acquisition:
                    continue
                if language and not entry_matches_language(entry, language):
                    continue
                results.append((catalog.name, entry))
        return results, errors


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
