from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import Config
from .download import download as download_file
from .download import filename_for, open_with_reader
from .models import Catalog, Entry, Feed
from .opds import OPDSClient, OPDSException, entry_matches_language

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    help="Browse OPDS catalogs from the terminal (TUI and CLI).",
    no_args_is_help=False,
    add_completion=False,
)
catalog_app = typer.Typer(help="Manage OPDS catalogs.")
config_app = typer.Typer(help="View or change settings.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(config_app, name="config")


def _client() -> OPDSClient:
    return OPDSClient()


def _resolve_feed(client: OPDSClient, config: Config, catalog: str | None, url: str | None) -> Feed:
    if url:
        return client.fetch(url)
    if not catalog:
        raise typer.BadParameter("Provide a CATALOG name or --url")
    entry = config.get_catalog(catalog)
    if not entry:
        err_console.print(f"[red]Unknown catalog:[/red] {catalog}")
        raise typer.Exit(1)
    return client.fetch(entry.url)


def _render_entries(feed: Feed, show_links: bool = False) -> None:
    if feed.title:
        console.print(f"[bold]{feed.title}[/bold]")
    if not feed.entries:
        console.print("[yellow]No entries.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", overflow="fold")
    table.add_column("Author", overflow="fold")
    table.add_column("Type")
    if show_links:
        table.add_column("Link", overflow="fold")
    for index, entry in enumerate(feed.entries, 1):
        if entry.best_acquisition:
            kind = entry.best_acquisition.type or "file"
            extra = entry.best_acquisition.href
        elif entry.is_navigation:
            kind = "nav"
            extra = entry.navigation_url or ""
        else:
            kind = "-"
            extra = ""
        row = [str(index), entry.title or "(untitled)", entry.author_str, kind]
        if show_links:
            row.append(extra)
        table.add_row(*row)
    console.print(table)


def _render_search_results(
    results: list[tuple[str, Entry]], errors: dict[str, str], show_links: bool = False
) -> None:
    if results:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Catalog")
        table.add_column("Title", overflow="fold")
        table.add_column("Author", overflow="fold")
        table.add_column("Type")
        if show_links:
            table.add_column("Link", overflow="fold")
        for index, (name, entry) in enumerate(results, 1):
            if entry.best_acquisition:
                kind = entry.best_acquisition.type or "file"
                extra = entry.best_acquisition.href
            elif entry.is_navigation:
                kind = "nav"
                extra = entry.navigation_url or ""
            else:
                kind = "-"
                extra = ""
            row = [str(index), name, entry.title or "(untitled)", entry.author_str, kind]
            if show_links:
                row.append(extra)
            table.add_row(*row)
        console.print(table)
    else:
        console.print("[yellow]No results.[/yellow]")
    for name, err in errors.items():
        err_console.print(f"[yellow]{name}:[/yellow] {err}")


@catalog_app.command("add")
def catalog_add(
    name: str = typer.Argument(..., help="Friendly name for the catalog"),
    url: str = typer.Argument(..., help="OPDS feed URL"),
    description: str = typer.Option("", "--description", "-d"),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="Replace if it exists"),
) -> None:
    """Add an OPDS catalog."""
    config = Config()
    try:
        config.add_catalog(
            Catalog(name=name, url=url, description=description),
            overwrite=overwrite,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"Added catalog [bold]{name}[/bold] -> {url}")


@catalog_app.command("remove")
def catalog_remove(
    name: str = typer.Argument(..., help="Catalog name"),
) -> None:
    """Remove an OPDS catalog."""
    config = Config()
    if not config.remove_catalog(name):
        err_console.print(f"[red]Unknown catalog:[/red] {name}")
        raise typer.Exit(1)
    console.print(f"Removed catalog [bold]{name}[/bold]")


@catalog_app.command("list")
def catalog_list() -> None:
    """List configured catalogs."""
    config = Config()
    if not config.catalogs:
        console.print(
            "[yellow]No catalogs configured.[/yellow] Add one with 'opds-tui catalog add'."
        )
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name")
    table.add_column("URL", overflow="fold")
    table.add_column("Description", overflow="fold")
    for catalog in config.catalogs:
        table.add_row(catalog.name, catalog.url, catalog.description)
    console.print(table)


@app.command("browse")
def browse(
    catalog: str | None = typer.Argument(None, help="Catalog name"),
    url: str | None = typer.Option(None, "--url", "-u", help="Fetch an arbitrary feed URL"),
    links: bool = typer.Option(False, "--links", "-l", help="Show entry links"),
) -> None:
    """Browse a catalog feed and list its entries."""
    config = Config()
    with _client() as client:
        try:
            feed = _resolve_feed(client, config, catalog, url)
        except OPDSException as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    _render_entries(feed, show_links=links)


@app.command("search")
def search(
    catalog: str | None = typer.Argument(None, help="Catalog name (omit with --all)"),
    query: str | None = typer.Argument(None, help="Search terms (title, author, text)"),
    all_catalogs: bool = typer.Option(False, "--all", help="Search every configured catalog"),
    language: str | None = typer.Option(
        None, "--language", "--lang", help="Filter by language (de/es/fr/it/...)"
    ),
    pages: int = typer.Option(1, "--pages", "-p", help="Max pages of results per catalog"),
    links: bool = typer.Option(False, "--links", "-l", help="Show entry links"),
) -> None:
    """Search a catalog (or all catalogs with --all) by text, title or author."""
    if all_catalogs and query is None:
        query, catalog = catalog, None
    if not query:
        raise typer.BadParameter("QUERY is required")
    config = Config()
    with _client() as client:
        if all_catalogs:
            try:
                results, errors = client.search_all(
                    config.catalogs, query, language=language, max_pages=pages
                )
            except OPDSException as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1)
            _render_search_results(results, errors, show_links=links)
            return
        if not catalog:
            raise typer.BadParameter("Provide a CATALOG name or --all")
        entry = config.get_catalog(catalog)
        if not entry:
            err_console.print(f"[red]Unknown catalog:[/red] {catalog}")
            raise typer.Exit(1)
        try:
            feed = client.fetch(entry.url)
            results = client.search(feed, query, max_pages=pages)
            results.entries = [
                e
                for e in results.entries
                if language is None or entry_matches_language(e, language)
            ]
        except OPDSException as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    _render_entries(results, show_links=links)


@app.command("get")
def get(
    catalog: str | None = typer.Argument(None, help="Catalog name (omit with --all)"),
    query: str | None = typer.Argument(None, help="Search terms"),
    index: int = typer.Option(1, "--index", "-i", help="Which result to download (1-based)"),
    all_catalogs: bool = typer.Option(False, "--all", help="Search every configured catalog"),
    language: str | None = typer.Option(
        None, "--language", "--lang", help="Filter by language (de/es/fr/it/...)"
    ),
    pages: int = typer.Option(1, "--pages", "-p", help="Max pages of results per catalog"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Destination directory"),
) -> None:
    """Search a catalog (or all with --all) and download a matching ebook."""
    if all_catalogs and query is None:
        query, catalog = catalog, None
    if not query:
        raise typer.BadParameter("QUERY is required")
    config = Config()
    dest = output or config.download_dir
    with _client() as client:
        if all_catalogs:
            try:
                results, errors = client.search_all(
                    config.catalogs, query, language=language, max_pages=pages
                )
            except OPDSException as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1)
            for name, err in errors.items():
                err_console.print(f"[yellow]{name}:[/yellow] {err}")
            if not results:
                err_console.print("[yellow]No results.[/yellow]")
                raise typer.Exit(1)
            if index < 1 or index > len(results):
                err_console.print(f"[red]Index out of range (1-{len(results)}).[/red]")
                raise typer.Exit(1)
            _resolve_and_download(client, results[index - 1][1], dest)
            return
        if not catalog:
            raise typer.BadParameter("Provide a CATALOG name or --all")
        entry = config.get_catalog(catalog)
        if not entry:
            err_console.print(f"[red]Unknown catalog:[/red] {catalog}")
            raise typer.Exit(1)
        try:
            feed = client.fetch(entry.url)
            results = client.search(feed, query, max_pages=pages)
            entries = [
                e
                for e in results.entries
                if language is None or entry_matches_language(e, language)
            ]
        except OPDSException as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        if not entries:
            err_console.print("[yellow]No results.[/yellow]")
            raise typer.Exit(1)
        if index < 1 or index > len(entries):
            err_console.print(f"[red]Index out of range (1-{len(entries)}).[/red]")
            raise typer.Exit(1)
        _resolve_and_download(client, entries[index - 1], dest)


@app.command("download")
def download(
    url: str = typer.Argument(..., help="Direct URL to an ebook file"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Destination directory"),
    name: str | None = typer.Option(None, "--name", "-n", help="Output filename"),
) -> None:
    """Download an ebook from a direct URL."""
    config = Config()
    dest = output or config.download_dir
    try:
        with console.status(f"Downloading {url}..."):
            path = download_file(url, dest, name)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]Download failed:[/red] {exc}")
        raise typer.Exit(1)
    console.print(f"Saved to [bold]{path}[/bold]")


def _download_entry(entry: Entry, dest: Path, reader: str | None = None) -> None:
    link = entry.best_acquisition
    if not link:
        err_console.print("[red]No acquisition link available.[/red]")
        raise typer.Exit(1)
    filename = filename_for(entry, link)
    try:
        with console.status(f"Downloading {entry.title}..."):
            path = download_file(link.href, dest, filename)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]Download failed:[/red] {exc}")
        raise typer.Exit(1)
    console.print(f"Saved [bold]{entry.title}[/bold] to [bold]{path}[/bold]")
    if reader:
        open_with_reader(path, reader)


def _resolve_and_download(client: OPDSClient, entry: Entry, dest: Path) -> None:
    match = entry
    if not match.best_acquisition and match.navigation_url:
        try:
            detail = client.fetch(match.navigation_url)
        except OPDSException as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        resolved = next((e for e in detail.entries if e.best_acquisition), None)
        if resolved:
            match = resolved
    _download_entry(match, dest)


@app.command("open")
def open_file(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="Ebook file to open"),
    reader: str | None = typer.Option(None, "--reader", "-r", help="Reader command"),
) -> None:
    """Open an ebook in the configured external reader."""
    config = Config()
    open_with_reader(path, reader or config.reader)
    console.print(f"Opened [bold]{path}[/bold]")


@config_app.command("show")
def config_show() -> None:
    """Show current settings and paths."""
    from .config import CATALOGS_FILE, CONFIG_DIR

    config = Config()
    console.print(f"Config dir:     {CONFIG_DIR}")
    console.print(f"Catalogs file:  {CATALOGS_FILE}")
    console.print(f"Download dir:   {config.download_dir}")
    console.print(f"Reader:         {config.reader}")
    console.print(f"Catalogs:       {len(config.catalogs)}")


@config_app.command("set")
def config_set(
    download_dir: Path | None = typer.Option(None, "--download-dir", "-d"),
    reader: str | None = typer.Option(None, "--reader", "-r"),
) -> None:
    """Update settings."""
    config = Config()
    if download_dir is not None:
        config.download_dir = download_dir.expanduser()
    if reader is not None:
        config.reader = reader
    config.save_settings()
    console.print("Settings saved.")


@app.command("tui")
def tui() -> None:
    """Launch the interactive TUI."""
    from .tui.app import OPDSApp

    OPDSApp().run()


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    """Launch the TUI when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        from .tui.app import OPDSApp

        OPDSApp().run()
