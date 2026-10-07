from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .models import Entry, Link

USER_AGENT = "opds-tui/0.1 (+https://github.com/anomalyco/opencode)"
TIMEOUT = 120.0

_EXT_BY_TYPE = {
    "application/epub+zip": ".epub",
    "application/pdf": ".pdf",
    "application/x-mobipocket-ebook": ".mobi",
    "application/vnd.amazon.ebook": ".azw",
    "text/plain": ".txt",
    "application/zip": ".zip",
}


def _sanitize(name: str) -> str:
    name = re.sub(r"[^\w\s.\-]", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:150] or "download"


def filename_for(entry: Entry, link: Link) -> str:
    base = _sanitize(f"{entry.title} - {entry.author_str}" if entry.author_str else entry.title)
    ext = _EXT_BY_TYPE.get(link.type, "")
    if not ext:
        path_ext = Path(unquote(urlparse(link.href).path)).suffix
        ext = path_ext if len(path_ext) <= 6 else ""
    return base + ext


def _unique_path(dest_dir: Path, filename: str) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def download(
    url: str,
    dest_dir: Path,
    filename: str | None = None,
    *,
    progress=None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    fallback = _sanitize(unquote(Path(urlparse(url).path).name)) or "download"
    target = _unique_path(dest_dir, filename or fallback)
    part = target.with_name(target.name + ".part")
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            received = 0
            with part.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
                    received += len(chunk)
                    if progress:
                        progress(received, total)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(target)
    return target


def open_with_reader(path: Path, reader: str) -> None:
    command = reader.split()
    if any("{}" in part for part in command):
        command = [part.replace("{}", str(path)) for part in command]
    else:
        command.append(str(path))
    subprocess.Popen(command)
