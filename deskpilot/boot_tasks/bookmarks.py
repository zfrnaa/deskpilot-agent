"""Floorp browser places.sqlite read-only bookmark auditor."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from pydantic import BaseModel, Field

from deskpilot.config import FloorpConfig

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "ref_src",
    "fbclid",
    "gclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "_hsenc",
    "_hsmi",
    "mkt_tok",
}


class BookmarkAuditResult(BaseModel):
    """Result of Floorp bookmark audit."""

    total_bookmarks: int = 0
    noisy_count: int = 0
    duplicate_groups_count: int = 0
    sample_noisy: list[dict] = Field(default_factory=list)
    sample_duplicates: list[dict] = Field(default_factory=list)
    profile_path: Path | None = None
    error: str | None = None


def normalize_url(url: str) -> str:
    """Normalize a URL by stripping tracking parameters, scheme, www, and trailing slashes."""
    if not url:
        return ""

    clean_url = url.strip()
    if "://" not in clean_url and not clean_url.startswith("//"):
        parsed = urlparse("//" + clean_url)
    else:
        parsed = urlparse(clean_url)

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path.rstrip("/")

    # Strip tracking query parameters
    query_params = parse_qsl(parsed.query, keep_blank_values=True)
    clean_params = [
        (k, v)
        for k, v in query_params
        if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
    ]
    sorted_params = sorted(clean_params, key=lambda x: (x[0], x[1]))
    clean_query = urlencode(sorted_params)

    normalized = f"{netloc}{path}"
    if clean_query:
        normalized += f"?{clean_query}"
    return normalized


def is_title_noisy(title: str | None, url: str) -> bool:
    """Check whether a bookmark title is noisy, placeholder, empty, or merely repeats the URL."""
    if not title or not title.strip():
        return True

    t = title.strip().lower()
    u = url.strip().lower()

    if t == u or t.rstrip("/") == u.rstrip("/"):
        return True

    if t.startswith("http://") or t.startswith("https://"):
        return True

    parsed = urlparse(u)
    if not parsed.netloc:
        parsed = urlparse("https://" + u)

    netloc = parsed.netloc
    path = parsed.path
    domain_path = (netloc + path).rstrip("/")
    domain_path_no_www = (netloc.removeprefix("www.") + path).rstrip("/")

    t_clean = t.rstrip("/")
    t_clean_no_www = t_clean.removeprefix("www.")
    if t_clean in (domain_path, domain_path_no_www) or t_clean_no_www in (domain_path, domain_path_no_www):
        return True

    u_no_proto = re.sub(r"^https?://", "", u).rstrip("/")
    u_no_proto_no_www = re.sub(r"^www\.", "", u_no_proto)
    if t_clean in (u_no_proto, u_no_proto_no_www) or t_clean_no_www in (u_no_proto, u_no_proto_no_www):
        return True

    return False


def find_floorp_profile(custom_path: Path | str | None = None) -> Path | None:
    """Locate the Floorp profile directory or places.sqlite database path.

    Searches custom_path if provided, else looks for Floorp default profiles in %APPDATA%/Floorp/Profiles.
    """
    if custom_path is not None:
        p = Path(custom_path).expanduser().resolve()
        if not p.exists():
            return None
        if p.is_file():
            return p.parent
        # If directory contains places.sqlite directly
        if (p / "places.sqlite").exists():
            return p
        # If directory is Profiles parent containing subdirectories
        candidates = list(p.glob("*.default-release"))
        if candidates and (candidates[0] / "places.sqlite").exists():
            return candidates[0]
        for sub in p.iterdir():
            if sub.is_dir() and (sub / "places.sqlite").exists():
                return sub
        return p

    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None

    profiles_dir = Path(appdata) / "Floorp" / "Profiles"
    if not profiles_dir.exists():
        return None

    candidates = list(profiles_dir.glob("*.default-release"))
    if candidates and (candidates[0] / "places.sqlite").exists():
        return candidates[0]

    for sub in profiles_dir.iterdir():
        if sub.is_dir() and (sub / "places.sqlite").exists():
            return sub

    if candidates:
        return candidates[0]

    return None


def _audit_places_db_sync(db_file: Path) -> BookmarkAuditResult:
    """Synchronously audit places.sqlite in read-only immutable URI mode."""
    if not db_file.exists():
        return BookmarkAuditResult(
            profile_path=db_file.parent,
            error=f"places.sqlite not found at {db_file}",
        )

    # Use SQLite URI read-only and immutable mode
    # immutable=1 prevents lock creation and journal checking
    db_uri = f"{db_file.resolve().as_uri()}?immutable=1&mode=ro"

    try:
        conn = sqlite3.connect(db_uri, uri=True)
        cur = conn.cursor()
        query = """
            SELECT b.id, p.url, b.title, b.parent, b.dateAdded
            FROM moz_bookmarks b
            JOIN moz_places p ON b.fk = p.id
            WHERE b.type = 1 AND p.url NOT LIKE 'place:%'
        """
        cur.execute(query)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return BookmarkAuditResult(
            profile_path=db_file.parent,
            error=f"Database error reading places.sqlite: {e}",
        )

    total_bookmarks = len(rows)
    sample_noisy: list[dict] = []
    noisy_count = 0
    grouped_duplicates: dict[str, list[dict]] = defaultdict(list)

    for r in rows:
        b_id = r[0]
        url = r[1] or ""
        title = r[2]

        # Check noisy title
        if is_title_noisy(title, url):
            noisy_count += 1
            if len(sample_noisy) < 5:
                sample_noisy.append({
                    "id": b_id,
                    "url": url,
                    "title": title or "",
                })

        # Group by normalized URL
        norm_url = normalize_url(url)
        if norm_url:
            grouped_duplicates[norm_url].append({
                "id": b_id,
                "url": url,
                "title": title or "",
            })

    duplicate_groups = {
        norm: items for norm, items in grouped_duplicates.items() if len(items) > 1
    }
    sample_duplicates: list[dict] = [
        {"url": norm, "count": len(items), "bookmarks": items}
        for norm, items in list(duplicate_groups.items())[:5]
    ]

    return BookmarkAuditResult(
        total_bookmarks=total_bookmarks,
        noisy_count=noisy_count,
        duplicate_groups_count=len(duplicate_groups),
        sample_noisy=sample_noisy,
        sample_duplicates=sample_duplicates,
        profile_path=db_file.parent,
        error=None,
    )


async def audit_floorp_bookmarks(
    profile_path: Path | None = None,
    config: FloorpConfig | None = None,
) -> BookmarkAuditResult:
    """Perform a read-only audit of Floorp browser bookmarks."""
    if config is not None:
        if not config.enabled:
            return BookmarkAuditResult()
        if profile_path is None and config.profile_path is not None:
            profile_path = config.profile_path

    resolved_profile = find_floorp_profile(profile_path)
    if resolved_profile is None:
        return BookmarkAuditResult(
            profile_path=Path(profile_path).expanduser().resolve() if profile_path else None,
            error=f"Floorp profile or places.sqlite not found at: {profile_path or 'default location'}",
        )

    if resolved_profile.is_file():
        db_file = resolved_profile
    else:
        db_file = resolved_profile / "places.sqlite"

    if not db_file.exists():
        return BookmarkAuditResult(
            profile_path=resolved_profile,
            error=f"places.sqlite not found at: {db_file}",
        )

    return await asyncio.to_thread(_audit_places_db_sync, db_file)
