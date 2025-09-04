#!/usr/bin/env python3
"""
Server to server Plex migration.

Features included in one tool:
1) Playlists: copy playlists from source to destination with name and order preserved
2) Collections: copy collections by name and membership
3) Metadata sync: optionally align select metadata fields and artwork from source items to destination items

Matching is performed by provider GUIDs across video libraries. Adds are batched
with fallbacks that work around quirky Plex API errors.

Install
    pip install plexapi python-dotenv requests

Env (optional)
    SRC_PLEX_URL, SRC_PLEX_TOKEN
    DEST_PLEX_URL, DEST_PLEX_TOKEN  (or PLEX_URL/PLEX_TOKEN)
    VERIFY_SSL=true|false

Examples
    # Migrate all non smart video playlists
    python migrate_plex.py \
      --source-url http://nas:32400 --source-token sk_... \
      --dest-url http://dest:32400 --dest-token dk_... \
      --replace --debug

    # Migrate collections only
    python migrate_plex.py --collections --no-playlists ...

    # Sync metadata and artwork too
    python migrate_plex.py --sync-metadata --artwork --fields summary,tagline,contentRating,originallyAvailableAt --lock-fields ...
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

import requests
import urllib3
from dotenv import load_dotenv
from plexapi.exceptions import BadRequest
from plexapi.server import PlexServer


# ------------------------------ Utils ------------------------------

def eprint(*args, **kwargs):
    """Robust stderr printer that avoids relying on print(file=...).

    Some sandboxes or Python environments may not support the 'file' keyword
    on print(). We write directly to sys.stderr, with fallbacks.
    """
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    msg = sep.join(str(a) for a in args) + end
    try:
        sys.stderr.write(msg)
    except Exception:
        try:
            # last resort: stdout
            sys.stdout.write(msg)
        except Exception:
            pass


def connect_plex(url: str, token: str, insecure: bool) -> PlexServer:
    sess = requests.Session()
    if insecure:
        sess.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return PlexServer(url, token, session=sess)


def collect_guids(item) -> List[str]:
    out = []
    try:
        for g in getattr(item, "guids", []) or []:
            gid = getattr(g, "id", None)
            if gid:
                out.append(gid.strip())
    except Exception:
        pass
    return out


def rating_key(it):
    try:
        return getattr(it, "ratingKey", None) or getattr(it, "rating_key", None)
    except Exception:
        return None


# ------------------------------ Index the destination ------------------------------

class DestIndex:
    def __init__(self) -> None:
        self.by_guid: Dict[str, object] = {}
        self.count_items = 0

    def add_item(self, it):
        for gid in collect_guids(it):
            self.by_guid[gid.lower()] = it
        self.count_items += 1


def build_destination_index(plex: PlexServer, debug: bool = False) -> DestIndex:
    idx = DestIndex()
    sections = [s for s in plex.library.sections() if s.TYPE.lower() in ("movie", "show")]
    for s in sections:
        try:
            eprint(f"Indexing destination section '{s.title}' ({s.TYPE})...")
            if s.TYPE.lower() == "movie":
                for it in s.all():
                    idx.add_item(it)
            else:
                for show in s.all():
                    try:
                        for ep in show.episodes():
                            idx.add_item(ep)
                    except Exception as ex:
                        eprint(f"  Warning: could not enumerate episodes for {show.title}: {ex}")
        except Exception as ex:
            eprint(f"Warning: failed to index section {s.title}: {ex}")
    eprint(f"Indexed {len(idx.by_guid)} GUIDs across ~{idx.count_items} destination items")
    return idx


# ------------------------------ Playlist helpers ------------------------------

def _coerce_to_media(plex: PlexServer, items: List[object], debug: bool = False) -> List[object]:
    out: List[object] = []
    for it in items:
        try:
            if hasattr(it, "_server"):
                out.append(it)
            elif isinstance(it, int) or (isinstance(it, str) and str(it).isdigit()):
                media = plex.fetchItem(f"/library/metadata/{it}")
                out.append(media)
            else:
                eprint(f"  Warning: cannot coerce item of type {type(it)} to Media, skipping")
        except Exception as ex:
            eprint(f"  Warning: failed to fetch Media for ratingKey={it}: {ex}")
    return out


def find_existing_playlist(plex: PlexServer, name: str):
    try:
        for pl in plex.playlists():
            if pl.title == name:
                return pl
    except Exception:
        pass
    return None


def create_playlist_with_batches(
    plex: PlexServer,
    name: str,
    items: List[object],
    replace: bool,
    batch_size: int,
    debug: bool,
) -> object:
    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i : i + n]

    existing = find_existing_playlist(plex, name)
    if existing and replace:
        eprint(f"Deleting existing playlist: {name}")
        existing.delete()
        existing = None

    if existing is None:
        if not items:
            raise RuntimeError("No items to create a playlist with")
        seed = items[:1]
        rest = items[1:]
        eprint(f"Creating playlist '{name}' with 1 seed item, then adding {len(rest)} in batches of {batch_size}")
        try:
            pl = plex.createPlaylist(name, _coerce_to_media(plex, seed))
        except BadRequest as br:
            if "Must include items to add" in str(br):
                eprint("Seed create rejected as empty. Trying manual create via URI.")
                seed_val = seed[0]
                rk = seed_val if isinstance(seed_val, int) or (isinstance(seed_val, str) and str(seed_val).isdigit()) else rating_key(seed_val)
                if rk is None:
                    raise
                uri = f"server://{plex.machineIdentifier}/com.plexapp.plugins.library/library/metadata/{rk}"
                plex.query(
                    "/playlists",
                    method=plex._session.post,
                    params={"type": "video", "title": name, "smart": 0, "uri": uri},
                )
                pl = find_existing_playlist(plex, name)
                if pl is None:
                    raise
            else:
                raise
        for chunk in chunks(rest, batch_size):
            try:
                if debug:
                    eprint(f"Adding batch of {len(chunk)} items to '{name}'...")
                chunk_objs = _coerce_to_media(plex, chunk, debug=debug)
                pl.addItems(chunk_objs)
            except BadRequest as br:
                if "Must include items to add" in str(br):
                    eprint("Batch add rejected as empty. Falling back to single item adds for this batch.")
                    for it in chunk:
                        try:
                            it_obj = it if hasattr(it, "_server") else plex.fetchItem(f"/library/metadata/{it}")
                            pl.addItems([it_obj])
                        except Exception as ex2:
                            rk = rating_key(it) if hasattr(it, "_server") else it
                            eprint(f"  Single add failed for ratingKey={rk}: {ex2}")
                else:
                    raise
        return pl

    # append to existing
    for chunk in chunks(items, batch_size):
        try:
            if debug:
                eprint(f"Appending batch of {len(chunk)} items to '{name}'...")
            chunk_objs = _coerce_to_media(plex, chunk, debug=debug)
            existing.addItems(chunk_objs)
        except BadRequest as br:
            if "Must include items to add" in str(br):
                eprint("Batch add rejected as empty. Falling back to single item adds for this batch.")
                for it in chunk:
                    try:
                        it_obj = it if hasattr(it, "_server") else plex.fetchItem(f"/library/metadata/{it}")
                        existing.addItems([it_obj])
                    except Exception as ex2:
                        rk = rating_key(it) if hasattr(it, "_server") else it
                        eprint(f"  Single add failed for ratingKey={rk}: {ex2}")
            else:
                raise
    return existing


# ------------------------------ Collections ------------------------------

def _remove_collection(plex: PlexServer, name: str, debug: bool = False) -> int:
    total = 0
    for s in plex.library.sections():
        if s.TYPE.lower() not in ("movie", "show"):
            continue
        try:
            hits = s.search(collection=name)
            for it in hits:
                try:
                    if hasattr(it, "removeCollection"):
                        it.removeCollection(name)
                    else:
                        it.editTags("collection", [name], remove=True)
                    total += 1
                except Exception as ex:
                    eprint(f"  Warning: failed to remove from {getattr(it, 'title', '<item>')}: {ex}")
        except Exception as ex:
            if debug:
                eprint(f"  Search failed in section {s.title} for collection '{name}': {ex}")
    return total


def _add_collection_to_items(name: str, items: List[object], debug: bool = False) -> int:
    ok = 0
    for it in items:
        try:
            if hasattr(it, "addCollection"):
                it.addCollection(name)
            else:
                existing = list(getattr(it, "collections", []) or [])
                titles = {getattr(c, "tag", None) or getattr(c, "title", None) for c in existing}
                newvals = list(titles | {name})
                it.editTags("collection", newvals, remove=False)
            ok += 1
        except Exception as ex:
            eprint(f"  Warning: failed to add '{name}' to {getattr(it, 'title', '<item>')}: {ex}")
    return ok


def migrate_collections(
    src: PlexServer,
    dest: PlexServer,
    include: Optional[str],
    exclude: Optional[str],
    rename_template: str,
    replace: bool,
    debug: bool,
    dry_run: bool,
):
    include_re = re.compile(include) if include else None
    exclude_re = re.compile(exclude) if exclude else None

    idx = build_destination_index(dest, debug=debug)

    migrated = 0
    for s in src.library.sections():
        if s.TYPE.lower() not in ("movie", "show"):
            continue
        try:
            colls = s.collections()
        except Exception as ex:
            eprint(f"Warning: could not list collections for section {s.title}: {ex}")
            continue
        for coll in colls:
            try:
                name = getattr(coll, "title", None) or "<untitled>"
                if include_re and not include_re.search(name):
                    if debug:
                        eprint(f"Skip collection '{name}' due to include filter")
                    continue
                if exclude_re and exclude_re.search(name):
                    if debug:
                        eprint(f"Skip collection '{name}' due to exclude filter")
                    continue

                items = coll.items()
                if debug:
                    eprint(f"Collection '{name}': {len(items)} items")

                dest_items: List[object] = []
                missing = 0
                seen = set()
                for it in items:
                    guids = [g.lower() for g in collect_guids(it)]
                    matched = None
                    for gid in guids:
                        matched = idx.by_guid.get(gid)
                        if matched:
                            break
                    if matched is not None:
                        rk = rating_key(matched)
                        if rk is None or rk in seen:
                            continue
                        seen.add(rk)
                        dest_items.append(matched)
                    else:
                        missing += 1

                dest_name = rename_template.format(name=name)
                if debug:
                    eprint(f"  Mapped {len(dest_items)} items for collection '{dest_name}', missing {missing}")

                if dry_run:
                    eprint(f"[DRY RUN] Would {'replace and ' if replace else ''}create collection '{dest_name}' with {len(dest_items)} items")
                    migrated += 1
                    continue

                if replace:
                    removed = _remove_collection(dest, dest_name, debug=debug)
                    if debug:
                        eprint(f"  Cleared existing membership for '{dest_name}' from {removed} items")

                added = _add_collection_to_items(dest_name, dest_items, debug=debug)
                eprint(f"Created or updated collection '{dest_name}' with {added} items. Missed {missing}.")
                migrated += 1
            except Exception as ex:
                eprint(f"Error migrating collection '{getattr(coll, 'title', '<unknown>') }': {ex}")

    eprint(f"Done. Migrated {migrated} collections.")


# ------------------------------ Metadata sync ------------------------------

SYNCABLE_FIELDS = [
    "summary",
    "tagline",
    "contentRating",
    "originallyAvailableAt",
    "titleSort",
]


def _diff_fields(src, dest, fields: List[str]) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    diffs = {}
    for f in fields:
        sv = getattr(src, f, None)
        dv = getattr(dest, f, None)
        if str(sv or "").strip() != str(dv or "").strip():
            diffs[f] = (sv, dv)
    return diffs


def _apply_fields(dest, values: Dict[str, object], lock: bool, debug: bool = False) -> None:
    try:
        dest.edit(**values)
        dest.save()
        if lock:
            for k in values.keys():
                try:
                    dest.lockField(k)
                except Exception:
                    pass
        if debug:
            eprint(f"  Applied fields: {list(values.keys())}")
    except Exception as ex:
        eprint(f"  Warning: failed to edit fields {list(values.keys())}: {ex}")


def _copy_artwork(src_server: PlexServer, src_item, dest_item, debug: bool = False):
    # Poster
    try:
        if getattr(src_item, "thumb", None):
            url = src_server.url(src_item.thumb)
            with src_server._session.get(url, stream=True) as r:
                r.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as fp:
                    for chunk in r.iter_content(1024 * 64):
                        fp.write(chunk)
                    tmp = fp.name
            dest_item.uploadPoster(tmp)
            if debug:
                eprint("  Poster copied")
    except Exception as ex:
        eprint(f"  Warning: failed to copy poster: {ex}")
    # Background art
    try:
        if getattr(src_item, "art", None):
            url = src_server.url(src_item.art)
            with src_server._session.get(url, stream=True) as r:
                r.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as fp:
                    for chunk in r.iter_content(1024 * 64):
                        fp.write(chunk)
                    tmp = fp.name
            dest_item.uploadArt(tmp)
            if debug:
                eprint("  Art copied")
    except Exception as ex:
        eprint(f"  Warning: failed to copy art: {ex}")


def sync_metadata(
    src: PlexServer,
    dest: PlexServer,
    fields: List[str],
    artwork: bool,
    lock_fields: bool,
    include: Optional[str],
    exclude: Optional[str],
    debug: bool,
    dry_run: bool,
):
    include_re = re.compile(include) if include else None
    exclude_re = re.compile(exclude) if exclude else None

    idx = build_destination_index(dest, debug=debug)
    sections = [s for s in src.library.sections() if s.TYPE.lower() in ("movie", "show")]

    changed = 0
    scanned = 0
    for s in sections:
        eprint(f"Scanning source section '{s.title}' for metadata sync...")
        items = s.all() if s.TYPE.lower() == "movie" else s.all()
        for it in items:
            scanned += 1
            title = getattr(it, "title", "<untitled>")
            if include_re and not include_re.search(title):
                continue
            if exclude_re and exclude_re.search(title):
                continue
            # match
            matched = None
            for gid in [g.lower() for g in collect_guids(it)]:
                matched = idx.by_guid.get(gid)
                if matched:
                    break
            if matched is None:
                continue
            diffs = _diff_fields(it, matched, fields)
            if diffs and debug:
                eprint(f"'{title}': diffs -> {list(diffs.keys())}")
            if dry_run:
                continue
            if diffs:
                values = {k: v[0] for k, v in diffs.items()}
                _apply_fields(matched, values, lock=lock_fields, debug=debug)
                changed += 1
            if artwork:
                _copy_artwork(src, it, matched, debug=debug)
    eprint(f"Metadata sync complete. Scanned {scanned} items. Updated {changed}.")


# ------------------------------ Playlists ------------------------------

def migrate_playlists(
    src: PlexServer,
    dest: PlexServer,
    include: Optional[str],
    exclude: Optional[str],
    materialize_smart: bool,
    rename_template: str,
    replace: bool,
    batch_size: int,
    debug: bool,
    dry_run: bool,
):
    playlists = src.playlists()
    eprint(f"Found {len(playlists)} playlists on source")

    include_re = re.compile(include) if include else None
    exclude_re = re.compile(exclude) if exclude else None

    idx = build_destination_index(dest, debug=debug)

    migrated = 0
    for pl in playlists:
        try:
            name = pl.title
            ptype = getattr(pl, "playlistType", None) or getattr(pl, "smartType", None) or ""
            is_smart = bool(getattr(pl, "smart", False))
            if include_re and not include_re.search(name):
                if debug:
                    eprint(f"Skip '{name}' due to include filter")
                continue
            if exclude_re and exclude_re.search(name):
                if debug:
                    eprint(f"Skip '{name}' due to exclude filter")
                continue
            if is_smart and not materialize_smart:
                eprint(f"Skipping smart playlist '{name}' (use --materialize-smart to copy as static)")
                continue
            if ptype and ptype.lower() not in ("video", "movie", "show"):
                eprint(f"Skipping non video playlist '{name}' of type '{ptype}'")
                continue

            src_items = pl.items()
            if debug:
                eprint(f"Playlist '{name}': {len(src_items)} items")

            dest_items: List[object] = []
            missing = []
            seen_rk = set()
            for it in src_items:
                rk_src = rating_key(it)
                if rk_src is not None and rk_src in seen_rk:
                    continue
                if rk_src is not None:
                    seen_rk.add(rk_src)
                guids = [g.lower() for g in collect_guids(it)]
                matched = None
                for gid in guids:
                    matched = idx.by_guid.get(gid)
                    if matched:
                        break
                if matched is not None:
                    dest_items.append(matched)
                else:
                    title = getattr(it, "title", None)
                    missing.append((title or "<untitled>", guids[0] if guids else None))

            if debug:
                eprint(f"  Matched {len(dest_items)} of {len(src_items)} items for '{name}'")
                if missing:
                    eprint(f"  Missing first 10: {missing[:10]}")

            dest_keys: List[int] = []
            seen_keys = set()
            for it in dest_items:
                rk = rating_key(it)
                if rk is not None and rk not in seen_keys:
                    dest_keys.append(rk)
                    seen_keys.add(rk)

            if debug:
                eprint(f"  Destination ratingKeys first 10: {dest_keys[:10]}")

            if dry_run:
                eprint(f"[DRY RUN] Would create playlist '{rename_template.format(name=name)}' with {len(dest_keys)} items")
                migrated += 1
                continue

            if not dest_keys:
                eprint(f"Warning: No destination items matched for '{name}'. Skipping create.")
                continue

            dest_name = rename_template.format(name=name)
            create_playlist_with_batches(dest, dest_name, dest_keys, replace=replace, batch_size=batch_size, debug=debug)
            eprint(f"Created '{dest_name}' with {len(dest_items)} items. Missed {len(missing)}.")
            migrated += 1
        except Exception as ex:
            eprint(f"Error migrating playlist '{getattr(pl, 'title', '<unknown>') }': {ex}")

    eprint(f"Done. Migrated {migrated} playlists.")


# ------------------------------ Self tests ------------------------------

def _run_self_tests() -> None:
    class _FakeMedia:
        def __init__(self, rk: int):
            self._server = object()
            self.ratingKey = rk
            self.guids = []
            self.summary = "A"
            self.tagline = "B"
            self.contentRating = "PG"
            self.originallyAvailableAt = "2000-01-01"
            self.titleSort = "T"
        def edit(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def save(self):
            pass
        def lockField(self, k):
            setattr(self, f"locked_{k}", True)

    class _FakePlex:
        def __init__(self):
            self.fetched = {}
        def fetchItem(self, path: str):
            rk = int(path.rsplit("/", 1)[-1])
            m = _FakeMedia(rk)
            self.fetched[rk] = m
            return m

    # rating_key helper
    fm = _FakeMedia(42)
    assert rating_key(fm) == 42
    class _Obj: pass
    o = _Obj(); o.rating_key = 7
    assert rating_key(o) == 7

    # _coerce_to_media from ints and strings
    fp = _FakePlex()
    res = _coerce_to_media(fp, [1, "2", _FakeMedia(3)])
    assert [rating_key(x) for x in res] == [1, 2, 3]

    # _diff_fields and _apply_fields
    src = _FakeMedia(1)
    dest = _FakeMedia(2)
    src.summary = "New summary"
    diffs = _diff_fields(src, dest, ["summary"])  # should detect difference
    assert "summary" in diffs
    _apply_fields(dest, {"summary": src.summary}, lock=True)
    assert dest.summary == "New summary" and getattr(dest, "locked_summary", False) is True

    # eprint should not raise in restricted environments
    ok = True
    try:
        eprint("self-test eprint")
    except Exception:
        ok = False
    assert ok

    print("Self tests passed")


# ------------------------------ CLI ------------------------------

def main():
    load_dotenv()

    p = argparse.ArgumentParser(description="Migrate playlists, collections, and optionally sync metadata between Plex servers")
    p.add_argument("--source-url", default=os.getenv("SRC_PLEX_URL"), required=False)
    p.add_argument("--source-token", default=os.getenv("SRC_PLEX_TOKEN"), required=False)
    p.add_argument("--dest-url", default=os.getenv("DEST_PLEX_URL") or os.getenv("PLEX_URL"), required=False)
    p.add_argument("--dest-token", default=os.getenv("DEST_PLEX_TOKEN") or os.getenv("PLEX_TOKEN"), required=False)

    # Playlists
    p.add_argument("--include", help="Regex, only playlists whose names match are migrated")
    p.add_argument("--exclude", help="Regex, playlists whose names match are skipped")
    p.add_argument("--materialize-smart", action="store_true", help="Copy smart playlists by materializing their current items as a static list")
    p.add_argument("--rename-template", default="{name}", help="New playlist name format, use {name} to insert the source name")

    # Collections
    p.add_argument("--collections", action="store_true", help="Also migrate collections")
    p.add_argument("--no-playlists", action="store_true", help="Skip playlists")
    p.add_argument("--collection-include", help="Regex, only collections whose names match are migrated")
    p.add_argument("--collection-exclude", help="Regex, collections whose names match are skipped")
    p.add_argument("--collection-rename-template", default="{name}", help="New collection name format, use {name} to insert the source name")

    # Metadata sync
    p.add_argument("--sync-metadata", action="store_true", help="Sync metadata fields from source to destination")
    p.add_argument("--fields", default=",".join(SYNCABLE_FIELDS), help=f"Comma list of fields to sync. Default: {','.join(SYNCABLE_FIELDS)}")
    p.add_argument("--artwork", action="store_true", help="Also copy poster and background art from source to destination")
    p.add_argument("--lock-fields", action="store_true", help="Lock fields after editing to preserve values against agent refreshes")
    p.add_argument("--meta-include", help="Regex on title, only items whose title matches will be synced")
    p.add_argument("--meta-exclude", help="Regex on title, items whose title matches will be skipped")

    # General
    p.add_argument("--replace", action="store_true", help="Replace destination playlist if it exists and clear collection before re adding")
    p.add_argument("--batch-size", type=int, default=100, help="Items are added to playlists in batches of this size")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--self-test", action="store_true", help="Run internal tests and exit")
    p.add_argument("--insecure", action="store_true", default=(os.getenv("VERIFY_SSL", "true").lower() in ["0", "false", "no"]))

    args = p.parse_args()

    if args.self_test:
        _run_self_tests()
        sys.exit(0)

    if not args.source_url or not args.source_token:
        eprint("Error: Source URL and token are required. Use --source-url and --source-token or set SRC_PLEX_URL and SRC_PLEX_TOKEN.")
        sys.exit(2)
    if not args.dest_url or not args.dest_token:
        eprint("Error: Destination URL and token are required. Use --dest-url and --dest-token or set DEST_PLEX_URL and DEST_PLEX_TOKEN.")
        sys.exit(2)

    eprint("Connecting to source Plex...")
    src = connect_plex(args.source_url, args.source_token, args.insecure)

    eprint("Connecting to destination Plex...")
    dest = connect_plex(args.dest_url, args.dest_token, args.insecure)

    # Playlists
    if not args.no_playlists:
        migrate_playlists(
            src=src,
            dest=dest,
            include=args.include,
            exclude=args.exclude,
            materialize_smart=args.materialize_smart,
            rename_template=args.rename_template,
            replace=args.replace,
            batch_size=args.batch_size,
            debug=args.debug,
            dry_run=args.dry_run,
        )

    # Collections
    if args.collections:
        migrate_collections(
            src=src,
            dest=dest,
            include=args.collection_include,
            exclude=args.collection_exclude,
            rename_template=args.collection_rename_template,
            replace=args.replace,
            debug=args.debug,
            dry_run=args.dry_run,
        )

    # Metadata sync
    if args.sync_metadata:
        field_list = [f.strip() for f in (args.fields or "").split(",") if f.strip()]
        sync_metadata(
            src=src,
            dest=dest,
            fields=field_list,
            artwork=args.artwork,
            lock_fields=args.lock_fields,
            include=args.meta_include,
            exclude=args.meta_exclude,
            debug=args.debug,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        eprint("Interrupted by user")
        sys.exit(130)
    except Exception as ex:
        eprint(f"Fatal error: {ex}")
        sys.exit(1)
