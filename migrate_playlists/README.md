# Plex Library Migration Tool (Playlists, Collections & Metadata)

A practical, scriptable way to **migrate playlists, collections, and selected metadata (optionally artwork)** from a **source Plex Media Server** to a **destination Plex Media Server**.

This repo includes a single Python script `migrate_plex.py` that talks to both servers directly—**no CSV exports required**—and matches items by **provider GUIDs** (e.g., `tmdb://`, `imdb://`, `tvdb://`, `plex://`).

> **Why this tool?**
> Traditional exports can break when libraries differ in path or agent configuration. This script indexes the destination by GUID and recreates lists/collections reliably, with batch-add workarounds for Plex API quirks.

---

## Features

* **Playlists**: Recreate playlists with the same **names and item order**.
* **Collections**: Recreate collection **memberships** by name (optional).
* **Metadata sync** (optional): Mirror chosen fields (e.g., summary, tagline, content rating, sort title, original air date).

  * **Artwork copy** (optional): Poster and background art.
  * **Field locking** (optional): Prevent agents from overwriting values.
* **Robust batching**: Creates playlists with a single seed, then adds items in batches. Falls back to single adds if Plex rejects a batch.
* **Filtering**: Regex-based include/exclude on playlist names, collection names, and item titles for metadata sync.
* **Dry run & debug**: Inspect planned changes before applying.

---

## Requirements

* Python **3.8+** recommended
* Packages: `plexapi`, `python-dotenv`, `requests`

```bash
pip install plexapi python-dotenv requests
```

---

## Quick Start

1. **Get tokens & URLs** for both servers.

   * Destination and source server **base URLs** (e.g., `http://dest:32400`, `http://nas:32400`).
   * **X-Plex-Token** for each server account.
2. Run the script with the URLs and tokens.

```bash
python migrate_plex.py \
  --source-url http://nas:32400 --source-token <SOURCE_TOKEN> \
  --dest-url   http://dest:32400 --dest-token   <DEST_TOKEN> \
  --replace --collections --batch-size 80 --debug
```

> Tip: You can set environment variables (`SRC_PLEX_URL`, `SRC_PLEX_TOKEN`, `DEST_PLEX_URL`, `DEST_PLEX_TOKEN`) or use a `.env` file via `python-dotenv`.

---

## Environment Variables (optional)

```
SRC_PLEX_URL, SRC_PLEX_TOKEN
DEST_PLEX_URL, DEST_PLEX_TOKEN   # or PLEX_URL / PLEX_TOKEN
VERIFY_SSL=true|false            # default true; set false (or use --insecure) for self-signed certs
```

With a `.env` file:

```
SRC_PLEX_URL=http://nas:32400
SRC_PLEX_TOKEN=...
DEST_PLEX_URL=http://dest:32400
DEST_PLEX_TOKEN=...
VERIFY_SSL=false
```

---

## Common Workflows

### 1) Playlists only

```bash
python migrate_plex.py \
  --source-url "$SRC_PLEX_URL" --source-token "$SRC_PLEX_TOKEN" \
  --dest-url   "$DEST_PLEX_URL" --dest-token   "$DEST_PLEX_TOKEN" \
  --replace --batch-size 80 --debug
```

### 2) Collections only

```bash
python migrate_plex.py \
  --source-url "$SRC_PLEX_URL" --source-token "$SRC_PLEX_TOKEN" \
  --dest-url   "$DEST_PLEX_URL" --dest-token   "$DEST_PLEX_TOKEN" \
  --collections --no-playlists --replace --debug
```

### 3) Playlists **and** collections

```bash
python migrate_plex.py \
  --source-url "$SRC_PLEX_URL" --source-token "$SRC_PLEX_TOKEN" \
  --dest-url   "$DEST_PLEX_URL" --dest-token   "$DEST_PLEX_TOKEN" \
  --collections --replace --batch-size 80 --debug
```

### 4) Metadata & artwork sync (no playlists)

```bash
python migrate_plex.py \
  --source-url "$SRC_PLEX_URL" --source-token "$SRC_PLEX_TOKEN" \
  --dest-url   "$DEST_PLEX_URL" --dest-token   "$DEST_PLEX_TOKEN" \
  --no-playlists --collections \
  --sync-metadata --artwork --lock-fields \
  --fields summary,tagline,contentRating,originallyAvailableAt,titleSort \
  --debug
```

> **Field locking** prevents future metadata refreshes from undoing synced values. Use carefully; you can unlock later in Plex Web.

---

## Matching Strategy

1. **GUID match** across all Movie and Show libraries on destination.
2. Destination is **indexed once** per run for speed.
3. For playlists: items are mapped in order; duplicates are avoided.
4. For collections: each collection name is created on destination and applied to matched items. Use `--replace` to first clear an existing collection’s membership.

> **Different paths?** Not a problem. Matching is based on GUIDs, so CIFS vs local paths, or NAS vs VM, won’t matter.

---

## Command Reference

```
--source-url, --source-token      Source Plex base URL & token
--dest-url,   --dest-token        Destination Plex base URL & token

Playlists:
  --include REGEX                 Only migrate playlist names that match
  --exclude REGEX                 Skip playlist names that match
  --materialize-smart             Copy smart playlists as static lists
  --rename-template '{name}'      Rename playlist on destination
  --batch-size N                  Batch size for adding items (default 100)
  --replace                       Replace existing playlist of same name

Collections:
  --collections                   Also migrate collections
  --no-playlists                  Skip playlists
  --collection-include REGEX      Only migrate collection names that match
  --collection-exclude REGEX      Skip collection names that match
  --collection-rename-template    Rename collection on destination

Metadata:
  --sync-metadata                 Sync metadata fields from source
  --fields a,b,c                  Fields to sync (default: summary,tagline,contentRating,originallyAvailableAt,titleSort)
  --artwork                       Also copy poster & background art
  --lock-fields                   Lock fields after editing
  --meta-include REGEX            Only sync items whose titles match
  --meta-exclude REGEX            Skip items whose titles match

General:
  --dry-run                       Do not make changes
  --debug                         Verbose logs
  --insecure                      Disable TLS verify (or set VERIFY_SSL=false)
  --self-test                     Run internal unit tests and exit
```

---

## Troubleshooting

### "Must include items to add when creating new playlist."

Plex can reject batch adds or even initial creates under certain payload conditions. The script:

* Seeds a playlist with **one** item
* Adds the rest in **batches** (configurable)
* Falls back to **single-item** adds when Plex returns the error
* If the **seed create** itself is rejected, a **manual URI** creation is attempted

If you still see the error, try a smaller `--batch-size` (e.g., `50` or `20`) and run with `--debug`.

### "'int' object has no attribute '\_server'"

This occurs if an API path expects Media objects. The script now **coerces ratingKeys into Media objects** before adding to playlists.

### Standard error not supported in environment

Some constrained environments may not support `print(..., file=sys.stderr)`. The script uses a robust `eprint()` that writes to `sys.stderr` (or `stdout` if needed) without relying on the `file=` parameter.

### Items don’t match

* Ensure both servers use compatible agents (e.g., **Plex Movie** / **TheTVDB/The Movie Database** variations).
* Refresh metadata for a sample item on the destination so it has the same provider GUIDs as the source.
* Run with `--debug` to see counts and what was missing.

---

## Development & Tests

Run built-in tests (no Plex servers required):

```bash
python migrate_plex.py --self-test
```

Expected output:

```
Self tests passed
```

### Contributing

* PRs welcome. Please include a brief description, reproduction steps, and logs with `--debug` if fixing bugs.
* Keep defaults conservative; make potentially destructive actions opt-in.

---

## FAQ

**Q: Will this copy watch history or user-specific data?**
A: No. This script focuses on playlists, collections, and selected item metadata/artwork.

**Q: Can I migrate smart playlists as smart playlists?**
A: The script **materializes** smart playlists as static lists when you pass `--materialize-smart`.

**Q: Can I limit metadata sync to certain libraries?**
A: Use `--meta-include`/`--meta-exclude` with patterns matching item titles, or run multiple passes with include filters.

---

## Acknowledgements

Thanks to the Plex community and the `plexapi` maintainers.

## Windows PowerShell examples

> Use `py` or `python` depending on your Windows setup. Backticks (`` ` ``) are the PowerShell line-continuation character.

### Set environment variables for the session

```powershell
# Required
$env:SRC_PLEX_URL = "http://nas:32400"
$env:SRC_PLEX_TOKEN = "<SOURCE_TOKEN>"
$env:DEST_PLEX_URL = "http://dest:32400"
$env:DEST_PLEX_TOKEN = "<DEST_TOKEN>"

# Optional: disable TLS verification for self-signed certs
$env:VERIFY_SSL = "false"
```

### Playlists only

```powershell
py ./migrate_plex.py `
  --source-url $env:SRC_PLEX_URL --source-token $env:SRC_PLEX_TOKEN `
  --dest-url   $env:DEST_PLEX_URL --dest-token   $env:DEST_PLEX_TOKEN `
  --replace --batch-size 80 --debug
```

### Collections only

```powershell
py ./migrate_plex.py `
  --source-url $env:SRC_PLEX_URL --source-token $env:SRC_PLEX_TOKEN `
  --dest-url   $env:DEST_PLEX_URL --dest-token   $env:DEST_PLEX_TOKEN `
  --collections --no-playlists --replace --debug
```

### Playlists **and** collections

```powershell
py ./migrate_plex.py `
  --source-url $env:SRC_PLEX_URL --source-token $env:SRC_PLEX_TOKEN `
  --dest-url   $env:DEST_PLEX_URL --dest-token   $env:DEST_PLEX_TOKEN `
  --collections --replace --batch-size 80 --debug
```

### Metadata & artwork sync (no playlists)

```powershell
py ./migrate_plex.py `
  --source-url $env:SRC_PLEX_URL --source-token $env:SRC_PLEX_TOKEN `
  --dest-url   $env:DEST_PLEX_URL --dest-token   $env:DEST_PLEX_TOKEN `
  --no-playlists --collections `
  --sync-metadata --artwork --lock-fields `
  --fields summary,tagline,contentRating,originallyAvailableAt,titleSort `
  --debug
```

### Self-test

```powershell
py ./migrate_plex.py --self-test
```

> Prefer one-liners? Drop the backticks and put everything on one line.
