# opds-tui

A terminal OPDS ebook browser with a Vim-style TUI and a scriptable CLI.
Browse public catalogs, search by title/author/text, download books, and open
them in your reader.

> **Great for AI agents.** Every operation is available as a plain CLI command
> with text output, so agents (Claude, opencode, scripts, etc.) can add
> catalogs, search, and fetch books without a browser. The TUI is there for
> humans; the CLI is the machine interface.

## Install

```bash
uv sync
uv run opds-tui catalog add gutenberg https://www.gutenberg.org/ebooks.opds/
uv run opds-tui            # launch the TUI
```

## CLI

```bash
opds-tui catalog add|list|remove ...     # manage catalogs
opds-tui browse [CATALOG] [--url URL]    # list feed entries
opds-tui search CATALOG QUERY            # search a catalog
opds-tui get CATALOG QUERY [-i N] [-o DIR]  # search + download
opds-tui download URL [-o DIR] [-n NAME] # download a direct URL
opds-tui open FILE                       # open in external reader
opds-tui config show|set ...             # paths and settings
```

## TUI keys

`j/k` move · `g/G` first/last · `h` back · `l`/`Enter` open · `Tab` switch pane
`/` search · `a` add catalog · `x` remove catalog · `d` download · `o` open in reader
`r` reload · `?` help · `q` quit

## Storage

Config in `~/.config/opds-tui/` (or OS equivalent, via `platformdirs`);
downloads in `~/Downloads/opds-tui/`. See `opds-tui config show`.
