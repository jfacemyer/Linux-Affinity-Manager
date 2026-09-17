"""Named, dated copies of a prefix's configuration.

A snapshot is the prefix's own settings, not the prefix: the `Settings`
directory, the loose `.dat` files beside it, and `sess.db`. Tens of kilobytes
against several gigabytes, which is what makes it something you take before
every risky change rather than once a year.

    <base>/Manager/snapshots/<slug>/<stamp>[-<label>]/

Stored as a directory copy rather than an archive, so it can be read, diffed
and partially restored by hand. An archive would be smaller and would make the
one thing you want at 2am -- "just give me back RecentFiles.xml" -- into a job.

Restoring across prefixes is allowed and useful: it is how the live prefix's
settings get into a test one. So a snapshot records which prefix it came from,
and a restore that crosses prefixes says so, because restoring into the wrong
one is the expensive mistake.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import maintenance, prefsseed, registry

# What belongs to a snapshot, relative to the version directory that holds
# Settings. Affinity keeps some state beside the directory rather than in it,
# and a "settings backup" that lost sess.db would be a surprise later.
SIBLING_FILES = ("sess.db",)
SIBLING_SUFFIXES = (".dat",)

MANIFEST = "snapshot.json"
STAMP = "%Y%m%d-%H%M%S"


class NoSettings(RuntimeError):
    """The prefix has no Affinity settings to snapshot yet."""


class NotASnapshot(ValueError):
    pass


@dataclass
class Snapshot:
    path: Path
    prefix: str             # the prefix it was taken from
    label: str
    taken: str              # the stamp, as written
    version: str            # the Affinity version folder, e.g. 3.0
    files: int = 0
    bytes: int = 0
    notes: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def when(self) -> str:
        """The stamp as a date a person can read.

        Directory names want YYYYmmdd-HHMMSS, which sorts correctly and is
        unambiguous; a column of "20260917 180041" is neither of those things
        to read. Falls back to the raw stamp for a directory this did not
        write."""
        try:
            return time.strftime("%Y-%m-%d %H:%M",
                                 time.strptime(self.taken, STAMP))
        except (ValueError, TypeError):
            return self.taken

    def __str__(self) -> str:
        return f"{self.when} {self.label}".strip() + f" — from {self.prefix}"


def snapshots_dir() -> Path:
    return registry.manager_dir() / "snapshots"


def dir_for(prefix_name: str) -> Path:
    """One directory per prefix, named by the same slug the prefix uses.

    registry.dir_name already validates and normalises, and substitutes rather
    than strips, so "Affinity 33" and "Affinity33" cannot collapse together
    here any more than they can among the prefixes themselves."""
    return snapshots_dir() / registry.dir_name(prefix_name)


def _source(prefix) -> prefsseed.Settings:
    found = prefsseed.settings_in(prefix)
    if not found:
        raise NoSettings(
            f"{prefix} has no Affinity settings yet. There is nothing to "
            "snapshot until Affinity has been started in it once.")
    return found[0]


def _siblings(version_dir: Path) -> list[Path]:
    """The loose state beside Settings that belongs with it."""
    out = []
    if not version_dir.is_dir():
        return out
    for f in sorted(version_dir.iterdir()):
        if not f.is_file():
            continue
        if f.name in SIBLING_FILES or f.suffix.lower() in SIBLING_SUFFIXES:
            out.append(f)
    return out


def _stamp() -> str:
    return time.strftime(STAMP)


def _safe_label(label: str) -> str:
    """A label that is still a directory name.

    Kept permissive -- people label these "before 3.3" and "known good" -- but
    a label cannot introduce a path separator or hide the snapshot with a dot."""
    cleaned = "".join(c if (c.isalnum() or c in " ._-") else "-"
                      for c in (label or "").strip())
    cleaned = "-".join(cleaned.split())
    return cleaned.strip("-.")[:60]


def take(prefix_name, prefix_path, label="") -> Snapshot:
    """Copy a prefix's settings aside, under a dated name.

    Does not require the prefix to be idle. Reading settings from a running
    Affinity can catch a half-written preferences file, which is a reason to
    prefer taking these when it is closed -- but refusing outright would make
    the snapshot unavailable at exactly the moment somebody wants one, just
    before doing something they are unsure about."""
    source = _source(prefix_path)
    stamp = _stamp()
    safe = _safe_label(label)
    dest = dir_for(prefix_name) / (f"{stamp}-{safe}" if safe else stamp)
    if dest.exists():
        raise NotASnapshot(f"{dest} already exists.")
    dest.mkdir(parents=True)

    shutil.copytree(source.path, dest / "Settings", symlinks=True)
    siblings = _siblings(source.path.parent)
    for f in siblings:
        shutil.copy2(f, dest / f.name)

    files, size, _ = prefsseed._measure(dest)
    snapshot = Snapshot(dest, prefix_name, safe, stamp, source.version,
                        files, size)
    (dest / MANIFEST).write_text(json.dumps({
        "prefix": prefix_name,
        "source": str(source.path),
        "version": source.version,
        "label": safe,
        "taken": stamp,
        "siblings": [f.name for f in siblings],
    }, indent=2) + "\n")
    return snapshot


def read_manifest(path: Path) -> Snapshot:
    path = Path(path)
    if not (path / "Settings").is_dir():
        raise NotASnapshot(f"{path} has no Settings directory in it.")
    data = {}
    try:
        data = json.loads((path / MANIFEST).read_text())
    except (OSError, ValueError):
        pass                    # an unreadable manifest is not a lost snapshot
    files, size, _ = prefsseed._measure(path)
    notes = []
    if not data:
        notes.append("no manifest; which prefix this came from is unknown")
    stamp = data.get("taken") or path.name.split("-")[0]
    return Snapshot(path, data.get("prefix") or "unknown",
                    data.get("label") or "", stamp,
                    data.get("version") or "unknown", files, size, notes)


def listing(prefix_name=None) -> list[Snapshot]:
    """Snapshots, newest first. One prefix's, or every prefix's."""
    roots = ([dir_for(prefix_name)] if prefix_name
             else sorted(p for p in _existing(snapshots_dir()) if p.is_dir()))
    out = []
    for root in roots:
        for path in sorted(_existing(root), reverse=True):
            if not path.is_dir():
                continue
            try:
                out.append(read_manifest(path))
            except NotASnapshot:
                continue
    out.sort(key=lambda s: s.taken, reverse=True)
    return out


def _existing(path: Path):
    try:
        return list(path.iterdir())
    except OSError:
        return []


@dataclass
class RestorePlan:
    snapshot: Snapshot
    into_prefix: str
    into_path: Path
    destination: Path
    crosses_prefixes: bool
    will_replace: int
    will_add: int

    def describe(self) -> list[str]:
        lines = [f"Restore {self.snapshot} into {self.into_prefix}.",
                 f"{self.will_replace} file(s) replaced, "
                 f"{self.will_add} added, in {self.destination}."]
        if self.crosses_prefixes:
            lines.append(
                f"This snapshot was taken from {self.snapshot.prefix}, not from "
                f"{self.into_prefix}. That is allowed and is how settings move "
                "between prefixes -- but check it is the one you meant.")
        if self.snapshot.version != "unknown":
            lines.append(f"Affinity version folder: {self.snapshot.version}.")
        lines += self.snapshot.notes
        return lines


def plan_restore(snapshot: Snapshot, into_prefix: str, into_path) -> RestorePlan:
    into_path = Path(into_path).expanduser()
    destination = prefsseed.destination_for(
        into_path, None if snapshot.version == "unknown" else snapshot.version)
    stand_in = prefsseed.Settings(snapshot.path, snapshot.path / "Settings",
                                  snapshot.version, snapshot.files,
                                  snapshot.bytes, 0.0)
    counts = prefsseed.plan(stand_in, destination)
    return RestorePlan(snapshot, into_prefix, into_path, destination,
                       snapshot.prefix != into_prefix,
                       len(counts.replaced), len(counts.added))


def restore(plan: RestorePlan, *, snapshot_first=True) -> list[str]:
    """Put a snapshot back, after putting the current settings somewhere safe.

    Refuses on a running prefix -- reading settings from one is a risk, writing
    them into one is a corruption. Takes a snapshot of what is there first and
    defaults to doing so, because the common way to lose a configuration is to
    restore the wrong snapshot over it.

    Written to a temporary directory beside the target and renamed into place,
    so an interrupted restore cannot leave half of one configuration beside
    half of another."""
    maintenance._require_idle(plan.into_path)
    notes: list[str] = []

    if snapshot_first:
        try:
            kept = take(plan.into_prefix, plan.into_path,
                        label="before-restore")
            notes.append(f"Current settings snapshotted as {kept.name}")
        except NoSettings:
            notes.append("Nothing was there to snapshot first")

    destination = plan.destination
    staging = destination.with_name(destination.name + ".restoring")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(plan.snapshot.path / "Settings", staging, symlinks=True)

    previous = destination.with_name(
        destination.name + ".replaced-" + _stamp())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.rename(previous)
        notes.append(f"Replaced settings kept at {previous.name}")
    staging.rename(destination)
    notes.append(f"Restored into {destination}")

    # The loose state beside Settings, if the snapshot carried any.
    for f in sorted(plan.snapshot.path.iterdir()):
        if not f.is_file() or f.name == MANIFEST:
            continue
        shutil.copy2(f, destination.parent / f.name)
        notes.append(f"Restored {f.name}")
    return notes


def remove(snapshot: Snapshot) -> Path:
    """Delete one snapshot. Checked, because this takes a path from a manifest.

    A snapshot outside the snapshots directory is not a snapshot, whatever its
    manifest says -- and the manifest is a file on disk that nothing stops
    somebody editing."""
    path = Path(snapshot.path).resolve()
    root = snapshots_dir().resolve()
    if not maintenance._is_inside(path, root):
        raise NotASnapshot(f"{path} is not inside {root}.")
    shutil.rmtree(path)
    return path
