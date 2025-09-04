#!/usr/bin/env python3
"""
find_sd_in_plex_library.py

Scan a Plex library for SD videos and print them.
An item is considered SD if its maximum available height is below the threshold (default 720).

Env vars are loaded from .env:
  PLEX_URL
  PLEX_API_TOKEN

Usage:
  python find_sd_in_plex_library.py "Movies"
  python find_sd_in_plex_library.py "TV Shows" --csv sd_tv.csv
  python find_sd_in_plex_library.py "Movies" --insecure
  python find_sd_in_plex_library.py "Movies" --paths-only
"""

import os
import sys
import csv
import argparse
import logging
from typing import Optional, List

from dotenv import load_dotenv
from requests import Session
from plexapi.server import PlexServer
from plexapi.library import LibrarySection
from plexapi.video import Movie, Episode

# ------------------------------- helpers -------------------------------- #

def res_to_height(res: Optional[str]) -> Optional[int]:
    """Convert Plex videoResolution to a numeric height when possible."""
    if not res:
        return None
    s = str(res).lower()
    mapping = {
        "4k": 2160, "uhd": 2160, "2160": 2160, "2160p": 2160,
        "1080": 1080, "1080p": 1080, "fhd": 1080,
        "720": 720, "720p": 720, "hd": 720,
        "576": 576, "576p": 576,
        "480": 480, "480p": 480, "sd": 480,
    }
    if s in mapping:
        return mapping[s]
    try:
        return int(s)
    except Exception:
        return None


def item_max_height(item) -> Optional[int]:
    """Return the maximum height across all media versions for a movie or episode."""
    heights: List[int] = []
    try:
        for m in getattr(item, "media", []) or []:
            h = None
            # Prefer explicit height on Media when present
            if getattr(m, "height", None):
                try:
                    h = int(m.height)
                except Exception:
                    h = None
            # Fallback to videoResolution mapping
            if not h:
                vr = getattr(m, "videoResolution", None)
                h = res_to_height(vr)
            # Last resort, inspect streams for video height
            if not h:
                try:
                    for p in getattr(m, "parts", []) or []:
                        for s in getattr(p, "streams", []) or []:
                            if getattr(s, "streamType", None) == 1 and getattr(s, "height", None):
                                h = max(h or 0, int(s.height))
                except Exception:
                    pass
            if h:
                heights.append(h)
    except Exception:
        return None
    if not heights:
        return None
    return max(heights)


def describe_episode(ep: Episode) -> str:
    try:
        s = getattr(ep, "seasonNumber", None) or getattr(ep.season(), "index", None)
        e = getattr(ep, "index", None)
        return f"{ep.grandparentTitle} S{s:02d}E{e:02d} - {ep.title}"
    except Exception:
        return f"{getattr(ep, 'grandparentTitle', 'Unknown Show')} - {getattr(ep, 'title', 'Unknown Episode')}"


def get_item_paths(item) -> List[str]:
    """Return absolute file paths for all parts of a movie or episode."""
    paths: List[str] = []
    try:
        for part in item.iterParts():
            p = getattr(part, "file", None)
            if p:
                paths.append(p)
    except Exception:
        pass
    return paths


# ------------------------------- core ----------------------------------- #

def find_sd_items(section: LibrarySection, min_hd_height: int = 720, log: logging.Logger = None):
    """
    Yield dictionaries describing SD items in the given library section.
    An item qualifies as SD if its maximum height is strictly below min_hd_height.
    """
    if log is None:
        log = logging.getLogger(__name__)

    if section.type == "movie":
        items = section.all()
        for mv in items:
            mh = item_max_height(mv)
            if mh is None:
                log.debug("No height info for movie: %s", mv.title)
                continue
            if mh < min_hd_height:
                yield {
                    "library": section.title,
                    "type": "movie",
                    "title": mv.title,
                    "year": getattr(mv, "year", ""),
                    "show_title": "",
                    "season": "",
                    "episode": "",
                    "episode_title": "",
                    "max_height": mh,
                    "ratingKey": getattr(mv, "ratingKey", ""),
                    "key": getattr(mv, "key", ""),
                    "paths": get_item_paths(mv),
                }

    elif section.type == "show":
        shows = section.all()
        for show in shows:
            try:
                episodes = show.episodes()
            except Exception as e:
                log.warning("Could not list episodes for show %s: %s", show.title, e)
                continue
            for ep in episodes:
                mh = item_max_height(ep)
                if mh is None:
                    log.debug("No height info for episode: %s", describe_episode(ep))
                    continue
                if mh < min_hd_height:
                    yield {
                        "library": section.title,
                        "type": "episode",
                        "title": show.title,
                        "year": getattr(show, "year", ""),
                        "show_title": show.title,
                        "season": getattr(ep, "seasonNumber", ""),
                        "episode": getattr(ep, "index", ""),
                        "episode_title": getattr(ep, "title", ""),
                        "max_height": mh,
                        "ratingKey": getattr(ep, "ratingKey", ""),
                        "key": getattr(ep, "key", ""),
                        "paths": get_item_paths(ep),
                    }
    else:
        log.warning("Library type %s is not supported for SD scan", section.type)


# ----------------------------- entry point ------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Find SD videos in a Plex library.")
    parser.add_argument("library_name", help="Plex library name, for example Movies or TV Shows")
    parser.add_argument("--csv", help="Optional path to write results as CSV")
    parser.add_argument("--insecure", action="store_true", help="Ignore TLS certificate verification")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--threshold", type=int, default=720,
                        help="Height threshold for HD. Items with max height below this are treated as SD. Default 720")
    parser.add_argument("--paths-only", action="store_true",
                        help="Only output absolute file paths for SD results, one per line. If --csv is set, write a one-column CSV with header 'path'.")
    args = parser.parse_args()

    logging.basicConfig(
        level=(logging.DEBUG if args.debug else (logging.WARNING if args.paths_only else logging.INFO)),
        format="%(levelname)s: %(message)s",
    )
    log = logging.getLogger("sd-scan")

    # Load env
    load_dotenv()
    baseurl = os.getenv("PLEX_URL")
    token = os.getenv("PLEX_API_TOKEN")

    if not baseurl or not token:
        log.error("Missing PLEX_URL or PLEX_API_TOKEN in environment. Create a .env file or export vars.")
        sys.exit(1)

    # Build session with optional insecure mode
    session = Session()
    if args.insecure:
        session.verify = False
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    try:
        plex = PlexServer(baseurl, token, session=session)
    except Exception as e:
        log.error("Failed to connect to Plex server at %s: %s", baseurl, e)
        sys.exit(2)

    # Locate library section
    try:
        section = plex.library.section(args.library_name)
    except Exception as e:
        # In paths-only mode we still show this once
        log.error("Could not open library %s: %s", args.library_name, e)
        try:
            libs = ", ".join([s.title for s in plex.library.sections()])
            log.info("Available libraries: %s", libs)
        except Exception:
            pass
        sys.exit(3)

    if not args.paths_only:
        log.info("Scanning library %s (%s) for SD videos", section.title, section.type)

    rows = list(find_sd_items(section, min_hd_height=args.threshold, log=log))

    if args.paths_only:
        # Unique, deterministic order
        seen = set()
        out_paths: List[str] = []
        for r in rows:
            for p in r.get("paths", []):
                if p and p not in seen:
                    seen.add(p)
                    out_paths.append(p)
        for p in out_paths:
            print(p)
        if args.csv:
            try:
                with open(args.csv, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["path"])
                    for p in out_paths:
                        w.writerow([p])
            except Exception as e:
                log.error("Failed to write CSV %s: %s", args.csv, e)
                sys.exit(4)
        return

    # Normal verbose output
    count = len(rows)
    if count == 0:
        log.info("No SD items found.")
        return

    for r in rows:
        if r["type"] == "movie":
            print(f"[MOVIE] {r['title']} ({r['year']}) - max height {r['max_height']} - ratingKey {r['ratingKey']}")
        else:
            s = r.get("season", "")
            e = r.get("episode", "")
            ep_label = f"S{int(s):02d}E{int(e):02d}" if s and e else ""
            print(f"[EPISODE] {r['show_title']} {ep_label} - {r['episode_title']} - max height {r['max_height']} - ratingKey {r['ratingKey']}")

    log.info("Found %d SD items", count)

    if args.csv:
        fieldnames = [
            "library", "type", "title", "year",
            "show_title", "season", "episode", "episode_title",
            "max_height", "ratingKey", "key",
        ]
        try:
            with open(args.csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            log.info("Wrote CSV to %s", args.csv)
        except Exception as e:
            log.error("Failed to write CSV %s: %s", args.csv, e)
            sys.exit(4)


if __name__ == "__main__":
    main()
