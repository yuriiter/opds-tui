from __future__ import annotations

import asyncio

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
)

from ..config import Config
from ..download import download as download_file
from ..download import filename_for, open_with_reader
from ..models import Catalog, Entry, Feed
from ..opds import OPDSClient, OPDSException
from .screens import AddCatalogScreen, HelpScreen, SearchScreen

TYPE_LABELS = {
    "application/epub+zip": "epub",
    "application/pdf": "pdf",
    "application/x-mobipocket-ebook": "mobi",
}


class OPDSApp(App):
    TITLE = "opds-tui"
    SUB_TITLE = "OPDS browser"

    CSS = """
    #body { height: 1fr; }
    #sidebar {
        width: 32; border: round $primary; padding: 0 1;
    }
    #sidebar > Label { text-style: bold; padding: 1 0 0 0; }
    #main { width: 1fr; }
    #feed-title { height: auto; padding: 0 1; text-style: bold; }
    #entries { height: 1fr; }
    #detail {
        height: 7; border: round $secondary; padding: 0 1; overflow: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("g", "go_top", "Top", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("h", "back", "Back"),
        Binding("l", "open", "Open", show=False),
        Binding("enter", "open", "Open"),
        Binding("o", "open_reader", "Open+Read"),
        Binding("d", "download", "Download"),
        Binding("slash", "search", "Search"),
        Binding("S", "search_all", "Search all"),
        Binding("n", "load_more", "Load more"),
        Binding("a", "add_catalog", "Add catalog"),
        Binding("x", "remove_catalog", "Remove catalog", show=False),
        Binding("r", "reload", "Reload"),
        Binding("tab", "focus_next_pane", "Switch pane"),
        Binding("question_mark", "help", "Help"),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = Config()
        self.client = OPDSClient()
        self.history: list[Feed] = []
        self.current_feed: Feed | None = None
        self.entries: list[Entry] = []
        self.catalog_label = ""
        self.entry_catalogs: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("Catalogs")
                yield ListView(id="catalogs")
            with Vertical(id="main"):
                yield Static("Select a catalog (press 'a' to add one)", id="feed-title")
                yield DataTable(id="entries", zebra_stripes=True, cursor_type="row")
                yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#entries", DataTable)
        table.add_columns("Catalog", "Title", "Author", "Type")
        self._populate_catalogs()
        self.query_one("#catalogs", ListView).focus()

    def on_unmount(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------ data
    def _populate_catalogs(self) -> None:
        view = self.query_one("#catalogs", ListView)
        view.clear()
        for catalog in self.config.catalogs:
            view.append(ListItem(Label(escape(catalog.name))))
        if self.config.catalogs:
            view.index = 0
        self._set_subtitle()

    def _set_subtitle(self) -> None:
        if self.current_feed and self.current_feed.title:
            self.sub_title = self.current_feed.title
        else:
            self.sub_title = f"{len(self.config.catalogs)} catalogs"

    def _display_feed(self, feed: Feed, *, catalogs: list[str] | None = None) -> None:
        self.current_feed = feed
        self.entries = list(feed.entries)
        if catalogs is not None:
            self.entry_catalogs = list(catalogs)
        else:
            self.entry_catalogs = [self.catalog_label] * len(self.entries)
        title = feed.title or feed.url or "Feed"
        hint = " (navigation)" if feed.is_navigation else ""
        self.query_one("#feed-title", Static).update(f"[bold]{escape(title)}[/bold]{hint}")
        table = self.query_one("#entries", DataTable)
        table.clear()
        for index, entry in enumerate(self.entries):
            table.add_row(
                escape(self.entry_catalogs[index] if index < len(self.entry_catalogs) else ""),
                escape(entry.title or "(untitled)"),
                escape(entry.author_str),
                self._type_label(entry),
            )
        if self.entries:
            table.move_cursor(row=0)
            self._show_detail(0)
        else:
            self.query_one("#detail", Static).update("[dim]No entries.[/dim]")
        table.focus()
        self._set_subtitle()

    @staticmethod
    def _type_label(entry: Entry) -> str:
        link = entry.best_acquisition
        if link:
            return TYPE_LABELS.get(link.type, link.type or "file")
        if entry.is_navigation:
            return "nav"
        return "-"

    def _show_detail(self, row: int) -> None:
        if row < 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        parts = [f"[bold]{escape(entry.title or '(untitled)')}[/bold]"]
        if entry.author_str:
            parts.append(f"[italic]{escape(entry.author_str)}[/italic]")
        if entry.updated:
            parts.append(f"[dim]{escape(entry.updated)}[/dim]")
        if entry.summary:
            summary = entry.summary.strip()
            if len(summary) > 400:
                summary = summary[:400] + "..."
            parts.append(escape(summary))
        if entry.best_acquisition:
            link = entry.best_acquisition
            parts.append(
                f"[green]acquisition:[/green] {escape(link.type or 'file')} "
                "(press d to download, o to open)"
            )
        elif entry.is_navigation:
            parts.append("[cyan]→ subsection: press enter/l to open[/cyan]")
        self.query_one("#detail", Static).update("\n".join(parts))

    # --------------------------------------------------------------- workers
    @work(exclusive=True, group="fetch")
    async def _fetch_feed(self, url: str, *, reset: bool = False, label: str = "") -> None:
        self.sub_title = f"Loading {label or url}..."
        try:
            feed = await asyncio.to_thread(self.client.fetch, url)
        except OPDSException as exc:
            self.notify(str(exc), severity="error", timeout=8)
            self._set_subtitle()
            return
        if reset:
            self.history = [feed]
        else:
            self.history.append(feed)
        self._display_feed(feed)

    @work(exclusive=True, group="search")
    async def _run_search(self, query: str) -> None:
        if not self.current_feed:
            self.notify("Load a catalog first.", severity="warning")
            return
        feed = self.current_feed
        self.sub_title = f"Searching '{query}'..."
        try:
            results = await asyncio.to_thread(self.client.search, feed, query)
        except OPDSException as exc:
            self.notify(str(exc), severity="error", timeout=8)
            self._set_subtitle()
            return
        self._display_feed(results)
        self.notify(f"{len(results.entries)} result(s) for '{query}'")

    @work(exclusive=True, group="search")
    async def _run_search_all(self, query: str) -> None:
        self.sub_title = f"Searching all catalogs for '{query}'..."
        try:
            results, errors = await asyncio.to_thread(
                self.client.search_all, self.config.catalogs, query
            )
        except OPDSException as exc:
            self.notify(str(exc), severity="error", timeout=8)
            self._set_subtitle()
            return
        entries = [entry for _, entry in results]
        catalogs = [name for name, _ in results]
        feed = Feed(title=f"Search all: {query}", entries=entries)
        self._display_feed(feed, catalogs=catalogs)
        message = f"{len(results)} result(s) for '{query}'"
        if errors:
            message += f" ({len(errors)} catalog(s) failed)"
        self.notify(message)

    @work(exclusive=True, group="download")
    async def _download_entry(self, entry: Entry, *, open_after: bool) -> None:
        link = entry.best_acquisition
        if not link:
            self.notify("No downloadable file for this entry.", severity="warning")
            return
        filename = filename_for(entry, link)
        dest = self.config.download_dir
        label = entry.title or filename
        self.sub_title = f"Downloading {label}..."

        def progress(received: int, total: int) -> None:
            pct = (received * 100 // total) if total else 0
            self.call_from_thread(self._set_progress, label, pct)

        try:
            path = await asyncio.to_thread(
                download_file, link.href, dest, filename, progress=progress
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Download failed: {exc}", severity="error", timeout=8)
            self._set_subtitle()
            return
        self.notify(f"Saved to {path}")
        self._set_subtitle()
        if open_after:
            try:
                open_with_reader(path, self.config.reader)
                self.notify(f"Opened in {self.config.reader}")
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Could not open reader: {exc}", severity="error")

    def _set_progress(self, label: str, pct: int) -> None:
        self.sub_title = f"Downloading {label}... {pct}%"

    # --------------------------------------------------------------- actions
    def action_move_down(self) -> None:
        focused = self.focused
        if isinstance(focused, (DataTable, ListView)):
            focused.action_cursor_down()

    def action_move_up(self) -> None:
        focused = self.focused
        if isinstance(focused, (DataTable, ListView)):
            focused.action_cursor_up()

    def action_go_top(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable) and focused.row_count:
            focused.move_cursor(row=0)
        elif isinstance(focused, ListView) and len(focused.children):
            focused.index = 0

    def action_go_bottom(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable) and focused.row_count:
            focused.move_cursor(row=focused.row_count - 1)
        elif isinstance(focused, ListView) and len(focused.children):
            focused.index = len(focused.children) - 1

    def action_back(self) -> None:
        if len(self.history) > 1:
            self.history.pop()
            self._display_feed(self.history[-1])
        else:
            self.notify("Nothing to go back to.", severity="warning")

    def action_open(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView):
            self._open_catalog_index(focused.index)
        elif isinstance(focused, DataTable) and self.entries:
            self._open_entry(focused.cursor_row)

    def _open_entry(self, row: int) -> None:
        if row < 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        if entry.best_acquisition:
            self._download_entry(entry, open_after=True)
        elif entry.is_navigation and entry.navigation_url:
            self._fetch_feed(entry.navigation_url, label=entry.title)
        else:
            self.notify("Entry has no link.", severity="warning")

    def _open_catalog_index(self, index: int | None) -> None:
        if index is None or index < 0 or index >= len(self.config.catalogs):
            return
        catalog = self.config.catalogs[index]
        self.catalog_label = catalog.name
        self._fetch_feed(catalog.url, reset=True, label=catalog.name)

    def action_download(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable) and self.entries:
            self._download_entry(self.entries[focused.cursor_row], open_after=False)

    def action_open_reader(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable) and self.entries:
            self._download_entry(self.entries[focused.cursor_row], open_after=True)

    def action_search(self) -> None:
        def done(query: str | None) -> None:
            if query:
                self._run_search(query)

        self.push_screen(SearchScreen(), done)

    def action_search_all(self) -> None:
        def done(query: str | None) -> None:
            if query:
                self._run_search_all(query)

        self.push_screen(SearchScreen(), done)

    def action_add_catalog(self) -> None:
        def done(catalog: Catalog | None) -> None:
            if catalog is None:
                return
            try:
                self.config.add_catalog(catalog)
            except ValueError as exc:
                self.notify(str(exc), severity="error")
                return
            self._populate_catalogs()
            self.notify(f"Added catalog '{catalog.name}'")
            self._open_catalog_index(len(self.config.catalogs) - 1)

        self.push_screen(AddCatalogScreen(), done)

    def action_remove_catalog(self) -> None:
        focused = self.focused
        view = self.query_one("#catalogs", ListView)
        index = focused.index if isinstance(focused, ListView) else view.index
        if index is None or index < 0 or index >= len(self.config.catalogs):
            return
        catalog = self.config.catalogs[index]
        self.config.remove_catalog(catalog.name)
        self._populate_catalogs()
        self.notify(f"Removed catalog '{catalog.name}'")

    def action_reload(self) -> None:
        if self.current_feed and self.current_feed.url:
            self._fetch_feed(self.current_feed.url, reset=True, label=self.current_feed.title)

    def action_load_more(self) -> None:
        if not self.current_feed or not self.current_feed.next_url:
            self.notify("No more entries.", severity="warning")
            return
        self._fetch_more(self.current_feed.next_url)

    @work(exclusive=True, group="fetch")
    async def _fetch_more(self, url: str) -> None:
        self.sub_title = "Loading more..."
        try:
            feed = await asyncio.to_thread(self.client.fetch, url)
        except OPDSException as exc:
            self.notify(str(exc), severity="error", timeout=8)
            self._set_subtitle()
            return
        self.current_feed.entries.extend(feed.entries)
        self.current_feed.links = feed.links
        table = self.query_one("#entries", DataTable)
        for entry in feed.entries:
            self.entries.append(entry)
            self.entry_catalogs.append(self.catalog_label)
            table.add_row(
                escape(self.catalog_label),
                escape(entry.title or "(untitled)"),
                escape(entry.author_str),
                self._type_label(entry),
            )
        self._set_subtitle()
        self.notify(f"Loaded {len(feed.entries)} more.")

    def action_focus_next_pane(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView):
            self.query_one("#entries", DataTable).focus()
        else:
            self.query_one("#catalogs", ListView).focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # --------------------------------------------------------------- messages
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail(event.cursor_row)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._open_entry(event.cursor_row)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._open_catalog_index(event.index)
