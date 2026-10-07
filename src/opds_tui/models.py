from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ACQUISITION_RELS = {
    "http://opds-spec.org/acquisition",
    "http://opds-spec.org/acquisition/open-access",
    "http://opds-spec.org/acquisition/buy",
    "http://opds-spec.org/acquisition/borrow",
    "http://opds-spec.org/acquisition/sample",
    "http://opds-spec.org/acquisition/subscribe",
}

IMAGE_REL = "http://opds-spec.org/image"
THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"
SUBSECTION_REL = "subsection"
NAVIGATION_REL = "http://opds-spec.org/navigation"
SEARCH_REL = "search"


@dataclass
class Catalog:
    name: str
    url: str
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "url": self.url, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> Catalog:
        return cls(
            name=data.get("name", ""),
            url=data.get("url", ""),
            description=data.get("description", ""),
        )


@dataclass
class Link:
    href: str
    rel: str = ""
    type: str = ""
    title: str = ""

    @property
    def is_acquisition(self) -> bool:
        return self.rel in ACQUISITION_RELS or self.rel.startswith(
            "http://opds-spec.org/acquisition"
        )

    @property
    def is_navigation(self) -> bool:
        if self.rel in (SUBSECTION_REL, NAVIGATION_REL):
            return True
        return "opds-catalog" in self.type or "atom+xml" in self.type or "opds+json" in self.type


@dataclass
class Entry:
    title: str = ""
    id: str = ""
    authors: list[str] = field(default_factory=list)
    summary: str = ""
    updated: str = ""
    language: str = ""
    links: list[Link] = field(default_factory=list)

    @property
    def navigation_url(self) -> str | None:
        for link in self.links:
            if link.is_navigation and not link.is_acquisition:
                return link.href
        return None

    @property
    def is_navigation(self) -> bool:
        return self.navigation_url is not None

    @property
    def acquisition_links(self) -> list[Link]:
        return [link for link in self.links if link.is_acquisition]

    @property
    def thumbnail(self) -> str | None:
        for link in self.links:
            if link.rel == THUMBNAIL_REL:
                return link.href
        for link in self.links:
            if link.rel == IMAGE_REL:
                return link.href
        return None

    @property
    def author_str(self) -> str:
        return ", ".join(self.authors)

    @property
    def best_acquisition(self) -> Link | None:
        links = self.acquisition_links
        if not links:
            return None
        priority = ("application/epub+zip", "application/pdf")
        for preferred in priority:
            for link in links:
                if link.type == preferred:
                    return link
        return links[0]


@dataclass
class Feed:
    title: str = ""
    id: str = ""
    updated: str = ""
    url: str = ""
    entries: list[Entry] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    is_navigation: bool = False

    @property
    def search_template(self) -> str | None:
        for link in self.links:
            if link.rel == SEARCH_REL and "{" in link.href:
                return link.href
        return None

    @property
    def opensearch_url(self) -> str | None:
        for link in self.links:
            if link.rel == SEARCH_REL and "opensearchdescription" in link.type:
                return link.href
        return None

    @property
    def next_url(self) -> str | None:
        for link in self.links:
            if link.rel == "next":
                return link.href
        return None

    @property
    def prev_url(self) -> str | None:
        for link in self.links:
            if link.rel == "previous":
                return link.href
        return None


def parse_updated(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None
