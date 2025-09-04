# Sync Plex Library Access By Library Name

This script syncs Plex library access from one server to another by matching **identically named** libraries.  
Source example: `noodle`. Destination example: `doodoo`.

It reads the set of libraries each user can access on the source server, finds the libraries with the same names on the destination server, then **adds** those destination libraries to the same users. It never removes access on the destination. Libraries that do not exist on the destination are skipped.

## Features

- Dry run by default so you can see the plan before changes occur
- Adds missing access on the destination server only
- Skips libraries that do not exist on the destination
- MFA aware login when using username and password
- Works even when a user is shared **all libraries** on a server
- Safe union update for each user on the destination server

## Requirements

- Python 3.8 or newer
- Packages: `plexapi` and `python-dotenv`

```bash
pip install plexapi python-dotenv
```

> If you see typing errors about `str | None`, use Python 3.10 or newer, or keep the provided script that uses `Optional[str]`.

## Installation

1. Place `sync_plex_shares_by_library_name.py` in your project directory.
2. Create a `.env` file next to the script and add your credentials. Token is preferred.

Example `.env`:

```ini
# Preferred
PLEX_ACCOUNT_TOKEN=your_plex_account_token

# Or username and password
# PLEX_USERNAME=you@example.com
# PLEX_PASSWORD=your_password

# Optional defaults
# PLEX_SERVER_SOURCE=noodle
# PLEX_SERVER_DEST=doodoo

# Optional MFA code if you want to provide it non-interactively
# PLEX_2FA_CODE=123456
```

## Authentication

The script tries these in order:

1. `PLEX_ACCOUNT_TOKEN` from `.env`  
2. `PLEX_USERNAME` and `PLEX_PASSWORD` from `.env`  
   - If MFA is required, it prompts for a one time code  
   - You can provide the code up front using `--mfa-code` or `PLEX_2FA_CODE`  
   - Use `--non-interactive` to disable prompts

## How It Works

- The script lists libraries on both servers.
- For each user that has access on the **source** server:
  - It reads the exact set of libraries the user can see on that server
  - It filters to libraries that also exist by name on the **destination**
  - It adds those destination libraries to the user on the destination
- If a user is shared **all libraries** on the source or destination, the script handles that correctly
- It never removes access from the destination

## Usage

Run from the directory that contains the script and `.env`.

### Basic, dry run

```bash
python sync_plex_shares_by_library_name.py
```

### Apply changes

```bash
python sync_plex_shares_by_library_name.py --apply
```

### Limit to specific users

Repeat `--only-user` for each target. You can pass usernames or emails.

```bash
python sync_plex_shares_by_library_name.py --only-user alice --only-user bob --apply
```

### Provide an MFA code up front

```bash
python sync_plex_shares_by_library_name.py --mfa-code 123456 --apply
```

### Non-interactive mode

```bash
python sync_plex_shares_by_library_name.py --non-interactive --apply
```

### Verbose debug

```bash
python sync_plex_shares_by_library_name.py --debug
```

## Windows PowerShell Examples

Open Windows Terminal or PowerShell in the folder with the script.

### Dry run with defaults

```powershell
python .\sync_plex_shares_by_library_name.py
```

### Apply changes

```powershell
python .\sync_plex_shares_by_library_name.py --apply
```

### Target one user

```powershell
python .\sync_plex_shares_by_library_name.py --only-user "chousto" --apply
```

### Target multiple users

```powershell
python .\sync_plex_shares_by_library_name.py --only-user "alice@example.com" --only-user "bob@example.com" --apply
```

### Provide MFA code in the command

```powershell
python .\sync_plex_shares_by_library_name.py --mfa-code 123456 --apply
```

### Disable prompts for automation

```powershell
python .\sync_plex_shares_by_library_name.py --non-interactive --apply
```

### Override servers for a one off run

```powershell
python .\sync_plex_shares_by_library_name.py --source "noodle" --dest "doodoo" --apply
```

### Create a `.env` file from PowerShell

```powershell
@'
PLEX_ACCOUNT_TOKEN=your_plex_account_token
PLEX_SERVER_SOURCE=noodle
PLEX_SERVER_DEST=doodoo
'@ | Set-Content -NoNewline .env
```

> You can also set environment variables for the current session:
>
> ```powershell
> $env:PLEX_ACCOUNT_TOKEN = "your_plex_account_token"
> $env:PLEX_SERVER_SOURCE = "noodle"
> $env:PLEX_SERVER_DEST   = "doodoo"
> python .\sync_plex_shares_by_library_name.py --apply
> ```

## Command Line Options

- `--source` Name of the source Plex server. Default uses `PLEX_SERVER_SOURCE` or `noodle`
- `--dest` Name of the destination Plex server. Default uses `PLEX_SERVER_DEST` or `doodoo`
- `--apply` Apply changes. Without this flag the script performs a dry run
- `--debug` Print additional details for troubleshooting
- `--only-user` Limit to specific users. Repeat for multiple users. Accepts username or email
- `--mfa-code` Provide a Plex one time code non-interactively
- `--non-interactive` Disable prompts. Useful for CI or scheduled tasks

## Notes and Caveats

- You must be the owner of the destination server to modify shares
- Server names must match exactly as they appear in your Plex account
- Library matching is case insensitive based on the displayed library title
- Duplicate library names on a server can be ambiguous. The script warns if it detects duplicates
- The script never removes access on the destination. It only adds access
- If a user already has the desired access on the destination, no change is made

## Troubleshooting

- **Auth error or MFA prompt loops**
  - Confirm your token is valid. Try logging out and back in on plex.tv and create a fresh token
  - If using username and password, try providing `--mfa-code` on the same command
  - Use `--debug` to see more detail

- **Server not found**
  - Verify the server names exactly as shown in your Plex account devices list

- **No changes reported**
  - Make sure the users actually have shares on the source
  - Ensure that matching library names exist on the destination

- **Typing error related to `str | None`**
  - Use Python 3.10 or newer, or keep the provided script that already uses `Optional[str]`

## Example Output

Dry run, single user:

```
Source server: noodle (xxxxxxxxxxxx)
Destination server: doodoo (yyyyyyyyyyyy)
Mode: DRY RUN (no changes will be made)

Scanning users and planning updates:

  User 'chousto': source has ['Kids Movies', 'Movies', 'TV Shows']
    Will add on dest for user 'chousto': ['Movies', 'TV Shows']

Summary:
  Users updated: 1
  Library grants added on destination: 2
  No changes were applied because this was a dry run. Use --apply to push updates.
```
