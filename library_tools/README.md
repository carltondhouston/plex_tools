# Find SD Videos in a Plex Library
`find_sd_in_plex_library.py`

A small Python utility that connects to your Plex Media Server, scans a single library by name, and reports items that are standard definition. By default, an item is considered SD if its maximum available video height is below 720.

- Reads connection settings from a `.env` file
- Supports movies and TV libraries
- Optional CSV export
- Optional output of absolute file paths only
- Works with self-signed TLS via `--insecure`

---

## Features

- Detects SD by the maximum available height across all versions of a title
- Threshold is configurable with `--threshold` (default 720)
- `--paths-only` prints one absolute file path per matching part
- CSV export with `--csv`
- Quiet output in `--paths-only` mode, with `--debug` available when needed

> Note: Paths are returned as seen by the Plex server. If the Plex server uses network mounts, the paths may be SMB or NFS style and may not exist on your local machine.

---

## Requirements

- Python 3.8 or newer
- A Plex Media Server you can reach over HTTP or HTTPS
- A Plex API token

Python packages:

```bash
pip install plexapi python-dotenv

