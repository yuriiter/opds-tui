from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ..models import Catalog


class AddCatalogScreen(ModalScreen[Catalog | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    AddCatalogScreen { align: center middle; }
    #dialog {
        width: 70; height: auto; padding: 1 2;
        background: $surface; border: thick $primary;
    }
    #dialog Label { margin-bottom: 1; }
    #dialog Input { margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 1; }
    #error { color: $error; height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Add OPDS catalog")
            yield Input(placeholder="Name", id="name")
            yield Input(placeholder="OPDS URL (http://...)", id="url")
            yield Input(placeholder="Description (optional)", id="description")
            yield Static("", id="error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="primary", id="add")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
            return
        name = self.query_one("#name", Input).value.strip()
        url = self.query_one("#url", Input).value.strip()
        description = self.query_one("#description", Input).value.strip()
        error = self.query_one("#error", Static)
        if not name or not url:
            error.update("Name and URL are required.")
            return
        if not url.startswith(("http://", "https://")):
            error.update("URL must start with http:// or https://")
            return
        self.dismiss(Catalog(name=name, url=url, description=description))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    SearchScreen { align: center middle; }
    #dialog {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: thick $primary;
    }
    #dialog Label { margin-bottom: 1; }
    """

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Search this catalog (title / author / text)")
            yield Input(value=self._initial, placeholder="query...", id="query")

    def on_mount(self) -> None:
        self.query_one("#query", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self.dismiss(query or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close"), ("?", "close", "Close")]

    CSS = """
    HelpScreen { align: center middle; }
    #dialog {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: thick $primary;
    }
    """

    HELP = """\
[bold]Navigation[/bold]
  j / k        move down / up
  g / G        go to first / last item
  h            go back in history
  l / enter    open selected item
  tab          switch between catalogs and entries
  r            reload current feed

[bold]Catalogs[/bold]
  a            add a catalog
  x            remove selected catalog
  enter        load selected catalog

[bold]Books[/bold]
  /            search current catalog
  d            download selected ebook
  o            download and open in reader
  esc          close this help / go back

[bold]General[/bold]
  ?            show help
  q            quit
"""

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.HELP, id="help")

    def action_close(self) -> None:
        self.dismiss(None)
