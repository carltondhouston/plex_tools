#!/usr/bin/env python3
"""
Sync Plex library access from one server to another by matching identical library names.

Behavior:
- For every user who has access to libraries on the source server, add access on the destination
  for any libraries that share the same name and exist on the destination.
- Preserve any existing access on the destination. Never remove access.
- Skip source libraries that do not exist on the destination.
- Default is dry run. Use --apply to push changes.

Auth:
- If PLEX_ACCOUNT_TOKEN is set, that is used.
- Otherwise uses PLEX_USERNAME and PLEX_PASSWORD.
- If MFA is required, the script will prompt for a 2FA code and retry sign in.
  You can also pass --mfa-code or set PLEX_2FA_CODE to avoid the prompt.
- Use --non-interactive to disable prompts.

Requires:
  pip install plexapi python-dotenv
"""

import argparse
import os
import sys
import getpass
from typing import Dict, List, Set, Tuple, Optional

from dotenv import load_dotenv
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
try:
    from plexapi.exceptions import Unauthorized, BadRequest
except Exception:  # pragma: no cover
    Unauthorized = Exception
    BadRequest = Exception


def _try_create_account(username: str, password: str, code: Optional[str]) -> MyPlexAccount:
    """
    Try different constructor signatures for MyPlexAccount to support a range of plexapi versions.
    """
    if code:
        # First try with 'code='
        try:
            return MyPlexAccount(username, password, code=code)
        except TypeError:
            pass
        # Then try with 'twoFactorCode='
        try:
            return MyPlexAccount(username, password, twoFactorCode=code)
        except TypeError:
            pass
        # Last, try positional with code as 3rd arg
        try:
            return MyPlexAccount(username, password, code)
        except TypeError:
            pass
    # Final attempt without code
    return MyPlexAccount(username, password)


def load_account(args) -> MyPlexAccount:
    token = os.getenv("PLEX_ACCOUNT_TOKEN")
    user = os.getenv("PLEX_USERNAME")
    pwd = os.getenv("PLEX_PASSWORD")
    preset_code = args.mfa_code or os.getenv("PLEX_2FA_CODE")

    if token:
        return MyPlexAccount(token=token)

    if not user or not pwd:
        raise RuntimeError("Missing credentials. Set PLEX_ACCOUNT_TOKEN or PLEX_USERNAME and PLEX_PASSWORD in .env.")

    # First attempt without MFA code unless one was supplied
    try:
        return _try_create_account(user, pwd, preset_code)
    except Unauthorized as e:
        # If Unauthorized and no code was provided, try prompting for MFA code
        msg = str(e).lower()
        mfa_hint = any(k in msg for k in ["two-factor", "2fa", "mfa", "code"])
        if not mfa_hint and preset_code:
            # Already tried with a code and still got Unauthorized
            raise

        if args.non_interactive:
            raise RuntimeError("MFA may be required but prompts are disabled. Supply --mfa-code or PLEX_2FA_CODE.") from e

        print("MFA appears to be required for this Plex account.")
        last_err = e
        for attempt in range(1, 3 + 1):
            code = getpass.getpass(f"Enter current Plex 2FA code (attempt {attempt} of 3): ").strip()
            if not code:
                print("Empty code, try again.")
                continue
            try:
                return _try_create_account(user, pwd, code)
            except Unauthorized as e2:
                print("Invalid or expired code. Trying again.")
                last_err = e2
                continue
            except BadRequest as e2:
                print("Login rejected. Trying again.")
                last_err = e2
                continue
        raise RuntimeError("Failed to authenticate after 3 MFA attempts.") from last_err
    except BadRequest:
        if args.non_interactive:
            raise
        print("Login failed, possibly due to MFA. Prompting for a code.")
        for attempt in range(1, 3 + 1):
            code = getpass.getpass(f"Enter current Plex 2FA code (attempt {attempt} of 3): ").strip()
            if not code:
                print("Empty code, try again.")
                continue
            try:
                return _try_create_account(user, pwd, code)
            except Exception:
                print("Invalid or expired code. Trying again.")
                continue
        raise RuntimeError("Failed to authenticate after 3 MFA attempts.")


def connect_server_by_name(account: MyPlexAccount, server_name: str) -> PlexServer:
    res = account.resource(server_name)
    if res is None:
        raise RuntimeError(f"Could not find server resource named '{server_name}' under this Plex account.")
    try:
        return res.connect()
    except Exception as e:
        raise RuntimeError(f"Failed to connect to server '{server_name}': {e}")


def sections_by_title(server: PlexServer) -> Dict[str, object]:
    """
    Return a dict mapping casefolded library title to the Section object.
    Warn if duplicates by title exist.
    """
    by_title = {}
    dupes = set()
    for sec in server.library.sections():
        key = sec.title.casefold()
        if key in by_title:
            dupes.add(sec.title)
        by_title[key] = sec
    if dupes:
        print(f"[WARN] Server '{server.friendlyName}' has duplicate library names detected: {sorted(dupes)}")
    return by_title


def _share_for_user_on_server(friend, server_machine_id: str, server_name: str):
    """
    Return the MyPlexServerShare object for this user on the given server, or None.
    Works across plexapi versions that expose .server(name) and/or .servers lists.
    """
    share = None
    # Preferred: use the helper that resolves by name
    if hasattr(friend, "server"):
        try:
            share = friend.server(server_name)
        except Exception:
            share = None
    # Fallback: scan servers list and match by machine id or name
    if not share and hasattr(friend, "servers"):
        for s in getattr(friend, "servers", []):
            mid = getattr(s, "machineIdentifier", None)
            nm = getattr(s, "name", None)
            if mid == server_machine_id or nm == server_name:
                share = s
                break
    return share


def friend_shared_sections_titles(
    account: MyPlexAccount,
    friend,
    target_server: PlexServer,
) -> Set[str]:
    """
    For a given friend or managed user, return the set of section titles they have on target_server.
    Honors 'allLibraries', otherwise enumerates share.sections().
    """
    titles: Set[str] = set()
    server_name = target_server.friendlyName
    server_machine_id = target_server.machineIdentifier

    try:
        share = _share_for_user_on_server(friend, server_machine_id, server_name)

        # One more fallback through account.user(...) in case 'friend' is a lightweight object
        if not share and hasattr(account, "user"):
            uname = getattr(friend, "username", None) or getattr(friend, "title", None)
            try:
                u = account.user(uname) if uname else None
                if u:
                    share = _share_for_user_on_server(u, server_machine_id, server_name)
            except Exception:
                pass

        if not share:
            return titles

        # If the user is shared all libraries on this server, treat as all titles
        if getattr(share, "allLibraries", False):
            for sec in target_server.library.sections():
                titles.add(sec.title)
            return titles

        # Otherwise enumerate the shared sections
        try:
            for sec in share.sections():
                t = getattr(sec, "title", None)
                if t:
                    titles.add(t)
        except Exception:
            # Some plexapi versions may need a defensive retry, but usually .sections() is fine
            return titles

    except Exception as e:
        friend_label = getattr(friend, "title", None) or getattr(friend, "username", None) or str(friend)
        print(f"[WARN] Could not read shared sections for user '{friend_label}' on '{server_name}': {e}")

    return titles


def ensure_union_share_on_destination(
    account: MyPlexAccount,
    friend,
    dest_server: PlexServer,
    desired_titles: Set[str],
    dest_title_to_section: Dict[str, object],
    dry_run: bool,
    debug: bool,
) -> Tuple[bool, List[str], List[str]]:
    """
    Compute union of current destination shares and desired_titles, then update friend shares on destination.
    Returns: (changed, added_titles, final_titles_sorted)
    """
    current_titles = friend_shared_sections_titles(account, friend, dest_server)
    if debug:
        print(f"      Current on dest: {sorted(current_titles)}")

    desired_titles_existing = {t for t in desired_titles if t.casefold() in dest_title_to_section}
    final_titles = set(current_titles) | set(desired_titles_existing)

    to_add = sorted(set(final_titles) - set(current_titles))
    if not to_add:
        if debug:
            print("      No changes needed.")
        return False, [], sorted(final_titles)

    final_sections = []
    missing = []
    for t in final_titles:
        sec = dest_title_to_section.get(t.casefold())
        if sec is None:
            missing.append(t)
        else:
            final_sections.append(sec)

    if missing and debug:
        print(f"      Skipping titles not on dest: {sorted(missing)}")

    friend_label = getattr(friend, "title", None) or getattr(friend, "username", None) or str(friend)
    print(f"    Will add on dest for user '{friend_label}': {to_add}")
    if not dry_run:
        try:
            # updateFriend sets the complete desired section list for this server
            account.updateFriend(friend, server=dest_server, sections=final_sections)
            print("      Applied.")
        except Exception as e:
            print(f"      ERROR applying updateFriend for '{friend_label}': {e}")
            return False, [], sorted(final_titles)

    return True, to_add, sorted(final_titles)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sync Plex shares by identical library names from one server to another.")
    parser.add_argument("--source", default=os.getenv("PLEX_SERVER_SOURCE", "noodle"), help="Source server name. Default: noodle or PLEX_SERVER_SOURCE")
    parser.add_argument("--dest", default=os.getenv("PLEX_SERVER_DEST", "doodoo"), help="Destination server name. Default: doodoo or PLEX_SERVER_DEST")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Without this flag, runs as a dry run.")
    parser.add_argument("--debug", action="store_true", help="Verbose debug output.")
    parser.add_argument("--only-user", action="append", default=[], help="Limit to one or more usernames or emails. Repeatable.")
    parser.add_argument("--mfa-code", default=None, help="Provide a Plex 2FA code up front. If omitted and MFA is required, you will be prompted unless --non-interactive is set.")
    parser.add_argument("--non-interactive", action="store_true", help="Disable interactive prompts, including MFA prompts.")
    args = parser.parse_args()

    try:
        account = load_account(args)
    except Exception as e:
        print(f"Auth error: {e}")
        sys.exit(1)

    # Connect servers
    try:
        src_server = connect_server_by_name(account, args.source)
        dest_server = connect_server_by_name(account, args.dest)
    except Exception as e:
        print(e)
        sys.exit(1)

    print(f"Source server: {src_server.friendlyName} ({src_server.machineIdentifier})")
    print(f"Destination server: {dest_server.friendlyName} ({dest_server.machineIdentifier})")
    if not args.apply:
        print("Mode: DRY RUN (no changes will be made)")
    else:
        print("Mode: APPLY changes")

    # Build section maps
    src_titles_to_sec = sections_by_title(src_server)
    dest_titles_to_sec = sections_by_title(dest_server)

    src_titles_set = {s.title for s in src_server.library.sections()}
    dest_titles_set = {s.title for s in dest_server.library.sections()}
    if args.debug:
        print(f"Source libraries: {sorted(src_titles_set)}")
        print(f"Destination libraries: {sorted(dest_titles_set)}")

    # Fetch all users this account knows about
    try:
        users = account.users()
    except Exception as e:
        print(f"ERROR: Could not retrieve account users: {e}")
        sys.exit(1)

    if args.only_user:
        lc_targets = {u.casefold() for u in args.only_user}

        def matches(u):
            cand = [
                getattr(u, "username", None),
                getattr(u, "title", None),
                getattr(u, "email", None),
            ]
            return any(c and c.casefold() in lc_targets for c in cand)

        users = [u for u in users if matches(u)]
        if not users:
            print("No users matched --only-user filters. Exiting.")
            sys.exit(0)

    total_changed = 0
    total_added = 0
    print("\nScanning users and planning updates:\n")
    for friend in users:
        friend_label = getattr(friend, "title", None) or getattr(friend, "username", None) or str(friend)
        try:
            src_titles_for_user = friend_shared_sections_titles(account, friend, src_server)
        except Exception as e:
            print(f"[WARN] Skipping user '{friend_label}' due to error reading source shares: {e}")
            continue

        if not src_titles_for_user:
            if args.debug:
                print(f"  User '{friend_label}': no shares on source, skipping.")
            continue

        desired_titles = {t for t in src_titles_for_user if t.casefold() in dest_titles_to_sec}

        print(f"  User '{friend_label}': source has {sorted(src_titles_for_user)}")
        if not desired_titles:
            print("    No matching libraries exist on destination. Skipping.")
            continue

        changed, added, final = ensure_union_share_on_destination(
            account,
            friend,
            dest_server,
            desired_titles,
            dest_titles_to_sec,
            dry_run=(not args.apply),
            debug=args.debug,
        )
        if changed:
            total_changed += 1
            total_added += len(added)

    print("\nSummary:")
    print(f"  Users updated: {total_changed}")
    print(f"  Library grants added on destination: {total_added}")
    if not args.apply:
        print("  No changes were applied because this was a dry run. Use --apply to push updates.")


if __name__ == "__main__":
    main()

