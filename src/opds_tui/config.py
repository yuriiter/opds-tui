from __future__ import annotations

import json
import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_downloads_dir

from .models import Catalog

APP_NAME = "opds-tui"

CONFIG_DIR = Path(user_config_dir(APP_NAME))
DATA_DIR = Path(user_data_dir(APP_NAME))
CATALOGS_FILE = CONFIG_DIR / "catalogs.json"
CONFIG_FILE = CONFIG_DIR / "config.json"


def default_download_dir() -> Path:
    downloads = Path(user_downloads_dir())
    return downloads / APP_NAME if downloads != Path.home() else DATA_DIR / "downloads"


DEFAULT_READER = "xdg-open" if os.name != "nt" else "start"


class Config:
    def __init__(self) -> None:
        self.catalogs: list[Catalog] = []
        self.download_dir: Path = default_download_dir()
        self.reader: str = DEFAULT_READER
        self.load()

    def load(self) -> None:
        if CATALOGS_FILE.exists():
            try:
                data = json.loads(CATALOGS_FILE.read_text())
                self.catalogs = [Catalog.from_dict(c) for c in data]
            except (json.JSONDecodeError, OSError):
                self.catalogs = []
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                if data.get("download_dir"):
                    self.download_dir = Path(data["download_dir"]).expanduser()
                if data.get("reader"):
                    self.reader = data["reader"]
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CATALOGS_FILE.write_text(json.dumps([c.to_dict() for c in self.catalogs], indent=2) + "\n")

    def save_settings(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(
                {"download_dir": str(self.download_dir), "reader": self.reader},
                indent=2,
            )
            + "\n"
        )

    def get_catalog(self, name: str) -> Catalog | None:
        for catalog in self.catalogs:
            if catalog.name.lower() == name.lower():
                return catalog
        return None

    def add_catalog(self, catalog: Catalog, *, overwrite: bool = False) -> None:
        existing = self.get_catalog(catalog.name)
        if existing:
            if not overwrite:
                raise ValueError(f"Catalog '{catalog.name}' already exists")
            self.catalogs[self.catalogs.index(existing)] = catalog
        else:
            self.catalogs.append(catalog)
        self.save()

    def remove_catalog(self, name: str) -> bool:
        catalog = self.get_catalog(name)
        if not catalog:
            return False
        self.catalogs.remove(catalog)
        self.save()
        return True
