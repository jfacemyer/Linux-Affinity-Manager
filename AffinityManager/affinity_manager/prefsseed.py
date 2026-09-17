"""Carrying preferences and recent files from one prefix into another.

A new prefix starts with the stock settings AffinityOnLinux installs, which is
right for somebody installing Affinity for the first time and wrong for
somebody who has been using it for a year and is building a second prefix to
test 3.3 in. What they want is their own workspace, their shortcuts and their
recent files -- not a fresh start they then have to undo by hand.

There are two ways to copy, and they are not interchangeable:

  FILL      Only files the destination does not already have. This is the rule
            the installer uses when it lays down stock settings, and the reason
            it uses it: replacing that directory wipes the user's preferences
            AND RecentFiles.xml, which is exactly the bug that started this.

  REPLACE   What the user is actually asking for when they say "copy my
            settings over". The destination's Settings directory is renamed
            aside first, with the date in its name, so the answer to "that was
            not what I wanted" is a rename rather than a reinstall.

Nothing here decides which. The caller asks, and the default offered is REPLACE
only because the user reached for this deliberately; FILL is what happens
without being asked for.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

FILL = "fill"
REPLACE = "replace"

# <prefix>/drive_c/users/<user>/AppData/Roaming/Affinity/Affinity/<version>/Settings
APPDATA_TAIL = ("AppData", "Roaming", "Affinity", "Affinity")


@dataclass
class Settings:
    """One Settings directory found inside a prefix."""

    prefix: Path
    path: Path
    version: str
    files: int
    bytes: int
    modified: float

    @property
    def has_recents(self) -> bool:
        return (self.path / "RecentFiles.xml").is_file()


def _measure(path: Path) -> tuple[int, int, float]:
    files = size = 0
    newest = 0.0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        files += 1
        size += st.st_size
        newest = max(newest, st.st_mtime)
    return files, size, newest


def settings_in(prefix) -> list[Settings]:
    """Every Affinity Settings directory in a prefix, newest first.

    Globbed rather than built from the current username: a prefix created on
    another machine, or by an installer run as a different user, has the other
    user's name in that path, and refusing to see it would be refusing to see
    the settings the user is pointing at."""
    p = Path(prefix).expanduser()
    users = p / "drive_c" / "users"
    out: list[Settings] = []
    if not users.is_dir():
        return out
    for user in sorted(users.iterdir()):
        root = user.joinpath(*APPDATA_TAIL)
        if not root.is_dir():
            continue
        for version in sorted(root.iterdir()):
            found = version / "Settings"
            if not found.is_dir():
                continue
            files, size, modified = _measure(found)
            if not files:
                continue
            out.append(Settings(p, found, version.name, files, size, modified))
    out.sort(key=lambda s: s.modified, reverse=True)
    return out


def sources(prefixes, exclude=None) -> list[Settings]:
    """Settings worth offering as a source, across several prefixes.

    The destination is excluded: offering to copy a prefix's settings onto
    itself is an invitation to a mistake, not a feature."""
    skip = Path(exclude).expanduser().resolve() if exclude else None
    out: list[Settings] = []
    for prefix in prefixes:
        p = Path(prefix).expanduser()
        try:
            if skip and p.resolve() == skip:
                continue
        except OSError:
            continue
        out += settings_in(p)
    out.sort(key=lambda s: s.modified, reverse=True)
    return out


def resolve_source(path) -> Settings | None:
    """Make sense of a location the user typed or browsed to.

    They may point at a prefix, at the Settings directory itself, or at the
    version directory above it. All three mean the same thing and all three are
    accepted -- being told "that is not a prefix" when you have just selected
    the exact directory you meant is no help to anybody."""
    p = Path(path).expanduser()
    if not p.is_dir():
        return None
    if p.name == "Settings":
        files, size, modified = _measure(p)
        if files:
            return Settings(p, p, p.parent.name, files, size, modified)
        return None
    if (p / "Settings").is_dir():
        return resolve_source(p / "Settings")
    found = settings_in(p)
    return found[0] if found else None


def destination_for(prefix, version=None) -> Path:
    """Where settings go in a prefix that may not have any yet.

    An existing Settings directory wins, because a prefix that has been started
    once has already decided its user name and version folder. Otherwise this
    falls back to the current user and 3.0 -- and the directory is not created
    here, so a caller that only wanted to look has not changed anything."""
    p = Path(prefix).expanduser()
    existing = settings_in(p)
    if existing:
        if version:
            for s in existing:
                if s.version == version:
                    return s.path
        return existing[0].path

    users = p / "drive_c" / "users"
    user = None
    if users.is_dir():
        names = [d for d in sorted(users.iterdir())
                 if d.is_dir() and d.name not in ("Public", "All Users")]
        user = names[0] if names else None
    if user is None:
        import getpass
        user = users / getpass.getuser()
    return user.joinpath(*APPDATA_TAIL) / (version or "3.0") / "Settings"


@dataclass
class SeedResult:
    added: list
    replaced: list
    kept: list
    saved_aside: Path | None


def plan(source: Settings, destination: Path) -> SeedResult:
    """What seed() would do in REPLACE mode. Writes nothing."""
    added, replaced = [], []
    for src in sorted(source.path.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(source.path)
        (replaced if (destination / rel).exists() else added).append(rel)
    return SeedResult(added, replaced, [], None)


def seed(source: Settings, destination, *, mode=REPLACE) -> SeedResult:
    """Copy one Settings directory into another prefix.

    In REPLACE mode the destination is renamed aside before anything is
    written, not deleted. Undoing a copy should be a rename, and the one thing
    nobody can get back by reinstalling is RecentFiles.xml."""
    dest = Path(destination).expanduser()
    saved_aside = None

    if mode == REPLACE and dest.is_dir() and any(dest.iterdir()):
        saved_aside = dest.with_name(
            "%s.before-copy-%s" % (dest.name, time.strftime("%Y-%m-%d-%H%M%S")))
        dest.rename(saved_aside)

    dest.mkdir(parents=True, exist_ok=True)
    added, replaced, kept = [], [], []
    for src in sorted(source.path.rglob("*")):
        rel = src.relative_to(source.path)
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            if mode == FILL:
                kept.append(rel)
                continue
            replaced.append(rel)
        else:
            added.append(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return SeedResult(added, replaced, kept, saved_aside)
