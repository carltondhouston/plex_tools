# Find SD Videos in a Plex Library

A small Python utility that connects to your Plex Media Server, scans a single library by name, and reports items that are standard definition. By default, an item is considered SD if its maximum available video height is below 720.

- Reads connection settings from a `.env` file
- Supports movies and TV libraries
- Optional CSV export
- Optional output of absolute file paths only
- Safe file deletion with confirmation, plus a no-confirm option
- Works with self-signed TLS via `--insecure`

---

## Features

- Detects SD by the maximum available height across all versions of a title
- Threshold is configurable with `--threshold` (default 720)
- `--paths-only` prints one absolute file path per matching part
- `--delete` removes matching files with a per-file confirmation prompt
- `--delete-no-confirm` removes matching files without prompting
- CSV export with `--csv`
- Quiet output in `--paths-only` and delete modes, with `--debug` available when needed

> Paths are returned as seen by the Plex server. If the Plex server uses network mounts, the paths may be SMB or NFS style and may not exist on your local machine.

---

## Requirements

- Python 3.8 or newer
- A Plex Media Server you can reach over HTTP or HTTPS
- A Plex API token

Python packages:

```bash
pip install plexapi python-dotenv
```

---

## Installation

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
python -m venv .venv
# Windows PowerShell
. .venv/Scripts/Activate.ps1
# macOS or Linux
source .venv/bin/activate
pip install -r requirements.txt  # if you add one
# or
pip install plexapi python-dotenv
```

Place the script in the repo root as `find_sd_in_plex_library.py` or adjust paths as needed.

---

## Configuration

Create a `.env` file in the project root:

```
PLEX_URL=http://your-plex-host:32400
PLEX_API_TOKEN=xxxxxxxxxxxxxxxxxxxx
```

Tips for the token:

- Sign in to the Plex web app and use the browser network inspector to find `X-Plex-Token` on API calls, or use any other preferred method to retrieve your token.
- The token is sensitive. Do not commit `.env` to version control.

---

## Usage

Basic usage:

```bash
python find_sd_in_plex_library.py "Movies"
```

Common options:

- `--csv PATH` write results to a CSV file
- `--insecure` skip TLS certificate verification
- `--threshold N` treat items with max height below `N` as SD (default 720)
- `--debug` verbose logging
- `--paths-only` print only absolute file paths for SD items
- `--delete` delete each SD file with confirmation
- `--delete-no-confirm` delete SD files without confirmation

### Examples, Bash

```bash
# Scan a movies library
python find_sd_in_plex_library.py "Movies"

# Scan TV and save a CSV
python find_sd_in_plex_library.py "TV Shows" --csv sd_tv.csv

# Connect to Plex over HTTPS with a self-signed certificate
python find_sd_in_plex_library.py "Movies" --insecure

# Consider anything below 1080p as SD
python find_sd_in_plex_library.py "Movies" --threshold 1080

# Print only absolute file paths (one per line)
python find_sd_in_plex_library.py "Movies" --paths-only

# Paths only plus CSV output with a single 'path' column
python find_sd_in_plex_library.py "TV Shows" --paths-only --csv sd_paths.csv

# Delete with confirmation per file
python find_sd_in_plex_library.py "Movies" --delete

# Delete without prompting
python find_sd_in_plex_library.py "TV Shows" --delete-no-confirm

# Combine stricter threshold with deletion and a CSV of targeted paths
python find_sd_in_plex_library.py "Movies" --threshold 1080 --delete --csv deleted_targets.csv
```

### Examples, Windows PowerShell

```powershell
# Activate virtual environment first if you created one
. .\.venv\Scripts\Activate.ps1

# Scan a library
python .ind_sd_in_plex_library.py "Movies"

# Save results to CSV
python .ind_sd_in_plex_library.py "TV Shows" --csv sd_tv.csv

# Use a self-signed server
python .ind_sd_in_plex_library.py "Movies" --insecure

# Treat anything below 1080p as SD
python .ind_sd_in_plex_library.py "Movies" --threshold 1080

# Print only file paths
python .ind_sd_in_plex_library.py "Movies" --paths-only

# Paths only with CSV export
python .ind_sd_in_plex_library.py "TV Shows" --paths-only --csv sd_paths.csv

# Delete with confirmation per file
python .ind_sd_in_plex_library.py "Movies" --delete

# Delete without prompting
python .ind_sd_in_plex_library.py "TV Shows" --delete-no-confirm
```

---

## Deletion modes

- `--delete` prompts for each file. Answer `y` or `yes` to delete, anything else to skip.
- `--delete-no-confirm` performs deletion without prompting.

Notes:

- Deletion uses `os.remove` on the filesystem. Files go straight to deletion, not to a recycle bin.
- Run the script on the Plex server or on a host that sees the same absolute paths.
- After removal, run a Plex library scan so the database reflects the missing files.

Safety checklist:

1. First run with no delete flags and inspect the output.
2. Optionally run `--paths-only` and redirect to a file for review.
3. When confident, use `--delete` to confirm each file, or `--delete-no-confirm` if you have already vetted the list.

---

## Output

### Normal console output
```
[MOVIE] The Big Sleep (1946) - max height 480 - ratingKey 12345
[EPISODE] Example Show S01E03 - The Episode Title - max height 576 - ratingKey 67890
```

### `--paths-only`
```
/mnt/media/movies/The Big Sleep (1946)/The Big Sleep (1946).mp4
/srv/tv/Example Show/Season 01/Example Show - s01e03 - The Episode Title.mkv
```

### CSV fields
When not using `--paths-only`, CSV contains:

```
library,type,title,year,show_title,season,episode,episode_title,max_height,ratingKey,key
```

With `--paths-only`, CSV contains a single header:

```
path
```

---

## How SD is determined

For each item, the script inspects available media and calculates the maximum known video height. If the maximum height is below the `--threshold` value, the item is reported as SD. This avoids flagging a movie as SD when there is also an HD or 4K version.

If you want a different rule, for example, report any item that has at least one SD version, you can change the logic in `find_sd_items` to check for any height below the threshold rather than the maximum.

---

## FAQ

**Will deletion handle filenames with spaces?**  
Yes. Deletion uses `os.remove(path)` with Python strings, which works with spaces and most Unicode characters.

---

## Troubleshooting

- **Missing PLEX_URL or PLEX_API_TOKEN**: ensure `.env` is present and loaded. You can also export these as environment variables.
- **Cannot open library**: verify the library name exactly matches Plex. The script prints the available sections on failure.
- **HTTPS errors with a private certificate**: use `--insecure`. You can also add your internal CA to the system trust store.
- **No results found**: confirm that the library contains SD material and that your threshold is correct.
- **Paths look unfamiliar**: paths are from the Plex server point of view. Docker bind mounts or remote shares will appear as seen by that host.
- **Windows read-only files**: clear the read-only attribute before deletion if needed. Long paths may require long path support.
- **After deletion, Plex still shows items**: perform a library scan to clean up missing media entries.

---

## Contributing

Issues and pull requests are welcome. Please describe your environment, library type, and a minimal reproduction if possible.
