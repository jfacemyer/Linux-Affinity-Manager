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

import getpass
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

    @property
    def extras(self) -> list:
        """What sits beside Settings in the version folder.

        Affinity keeps the user's workspace layouts and keyboard shortcuts
        here, not inside Settings. Copying only Settings across produced a
        MIXED configuration in the new prefix -- the source's preferences and
        recents next to the destination's workspaces -- which is worse than
        either of them on its own."""
        version = self.path.parent
        if not version.is_dir():
            return []
        return [e for e in sorted(version.iterdir()) if e.name != "Settings"]


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


# The accounts Wine creates that are never the person using it. The installer
# has its own copy of this list; both are here so the two agree about which
# user's AppData an Affinity install lives under.
NOT_A_PERSON = ("Public", "Default", "All Users", "Default User")


def destination_for(prefix, version=None) -> Path:
    """Where settings go in a prefix that may not have any yet.

    An existing Settings directory wins, because a prefix that has been started
    once has already decided its user name and version folder. Otherwise the
    current login name is preferred if Wine made a directory for it -- which it
    does -- and only then does this fall back to whatever else is there.

    The skip list matches the installer's. They used to differ: this one let
    "Default" and "Default User" through and then took the alphabetically
    first, so on a prefix holding both Default and joshua it chose Default,
    and settings were written where nothing would ever read them. The directory
    is not created here, so a caller that only wanted to look has changed
    nothing."""
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
                 if d.is_dir() and d.name not in NOT_A_PERSON]
        me = getpass.getuser()
        user = next((d for d in names if d.name == me), names[0] if names else None)
    if user is None:
        user = users / getpass.getuser()
    return user.joinpath(*APPDATA_TAIL) / (version or "3.0") / "Settings"


@dataclass
class SeedResult:
    added: list
    replaced: list
    kept: list
    saved_aside: Path | None
    extras: list = None

    def __post_init__(self):
        if self.extras is None:
            self.extras = []


def plan(source: Settings, destination: Path) -> SeedResult:
    """What the user will end up with. Writes nothing.

    "replaced" means the destination has a file of that name today and will
    have the source's afterwards. It does NOT describe seed()'s mechanism: in
    REPLACE mode the whole destination folder is renamed aside first, so
    nothing is overwritten in place and nothing is lost. The old docstring
    called this "what seed() would do in REPLACE mode", which was wrong in a
    way that mattered -- it made the count look like a count of destroyed
    files."""
    added, replaced = [], []
    for src in sorted(source.path.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(source.path)
        (replaced if (destination / rel).exists() else added).append(rel)
    return SeedResult(added, replaced, [], None,
                      [e.name for e in source.extras])


def aside_of(destination) -> Path | None:
    """The most recent .before-copy- directory beside this Settings folder.

    For the message shown when a copy fails. By then REPLACE has already
    renamed the old settings aside, so the prefix has no Settings folder and
    the only copy is under a name the user has never seen -- which is not
    something to leave them to work out."""
    dest = Path(destination).expanduser()
    try:
        found = [p for p in dest.parent.iterdir()
                 if p.name.startswith(dest.name + ".before-copy-")]
    except OSError:
        return None
    return max(found, key=lambda p: p.name) if found else None


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

    # And the workspaces and shortcuts, which live beside Settings rather than
    # inside it. Copying only Settings left the new prefix with the source's
    # preferences and recents next to its own workspaces -- a configuration
    # that had never existed anywhere.
    extras = []
    version_dir = dest.parent
    for entry in source.extras:
        target = version_dir / entry.name
        if target.exists() or target.is_symlink():
            if mode == FILL:
                kept.append(Path("..") / entry.name)
                continue
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        version_dir.mkdir(parents=True, exist_ok=True)
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True)
        else:
            shutil.copy2(entry, target, follow_symlinks=False)
        extras.append(entry.name)

    return SeedResult(added, replaced, kept, saved_aside, extras)
