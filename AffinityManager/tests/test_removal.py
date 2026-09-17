"""Removing a prefix, and the snapshots that outlive it.

The two properties worth protecting. Nothing unattributable is ever removed --
AffinityOnLinux's own Affinity.desktop is reported as left alone, not deleted.
And the confirmation is the list: every row that would go is a row, so there is
no summary for the one you would have objected to to hide behind.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from affinity_manager import (  # noqa: E402
    desktopentry, hoststate, prefixlog, prefsseed, registry, removal, snapshots,
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A throwaway XDG data home and manager directory."""
    data = tmp_path / "data"
    (data / "applications").mkdir(parents=True)
    (data / "mime" / "packages").mkdir(parents=True)
    (data / "icons").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path / "meta")
    (tmp_path / "meta").mkdir()
    monkeypatch.setattr(desktopentry, "refresh_menu", lambda: None)

    prefix = tmp_path / "Working"
    (prefix / "drive_c" / "windows").mkdir(parents=True)
    (prefix / "dosdevices").mkdir(parents=True)
    (prefix / "system.reg").write_text("WINE REGISTRY\n")

    reg = registry.Registry(tmp_path / "meta" / "prefixes.json")
    reg.add("Working", str(prefix))
    return type("Host", (), {"tmp": tmp_path, "data": data,
                             "prefix": prefix, "reg": reg})()


def settings_in(prefix: Path, user="joshua", version="3.0") -> Path:
    s = prefix.joinpath("drive_c", "users", user, *prefsseed.APPDATA_TAIL,
                        version, "Settings")
    s.mkdir(parents=True)
    (s / "preferences.dat").write_text("mine")
    (s / "RecentFiles.xml").write_text("<recent>a year of work</recent>")
    (s.parent / "sess.db").write_text("session")
    (s.parent / "loose.dat").write_text("beside Settings")
    return s


def kinds(plan):
    return [i.kind for i in plan.items]


# ── the plan ───────────────────────────────────────────────────────────────

def test_the_plan_lists_the_directory_and_the_registry_row(host):
    plan = removal.plan(host.reg, "Working")
    assert "prefix" in kinds(plan) and "registry" in kinds(plan)
    assert plan.size > 0


def test_the_plan_includes_the_menu_entry_it_wrote(host):
    from affinity_manager import commands as commands_mod

    command = commands_mod.for_prefix(host.prefix)[0]
    desktopentry.write("Working", command)
    plan = removal.plan(host.reg, "Working")
    assert "menu" in kinds(plan)


def test_a_desktop_file_that_is_not_ours_is_reported_not_offered(host):
    """AffinityOnLinux writes Affinity.desktop with a fixed name and every
    install rewrites it. It is not the manager's to remove."""
    foreign = host.data / "applications" / "Affinity.desktop"
    foreign.write_text("[Desktop Entry]\nName=Affinity\nExec=wine\n")
    plan = removal.plan(host.reg, "Working")
    assert all(i.path != foreign for i in plan.items)
    assert any(a.path == foreign for a in plan.left_alone)


def test_the_prefix_log_is_offered(host):
    prefixlog.write("Working", "something happened")
    plan = removal.plan(host.reg, "Working")
    assert "log" in kinds(plan)


def test_snapshots_are_listed_but_not_ticked(host):
    """Losing the backups of a thing along with the thing is the wrong
    default, and they are kilobytes."""
    settings_in(host.prefix)
    snapshots.take("Working", host.prefix, "before 3.3")
    plan = removal.plan(host.reg, "Working")
    backup = [i for i in plan.items if i.kind == "settings-backup"]
    assert len(backup) == 1 and backup[0].default is False
    assert backup[0] not in plan.chosen()


def test_a_prefix_whose_directory_has_gone_still_plans(host):
    import shutil
    shutil.rmtree(host.prefix)
    plan = removal.plan(host.reg, "Working")
    row = [i for i in plan.items if i.kind == "prefix"][0]
    assert row.default is False and "Not there" in row.detail
    assert "registry" in kinds(plan)


def test_an_unknown_prefix_is_a_keyerror_not_an_empty_plan(host):
    with pytest.raises(KeyError):
        removal.plan(host.reg, "Nonexistent")


# ── applying it ────────────────────────────────────────────────────────────

def test_only_the_ticked_rows_are_acted_on(host):
    prefixlog.write("Working", "keep me")
    plan = removal.plan(host.reg, "Working")
    only_registry = [i for i in plan.items if i.kind == "registry"]
    removal.apply(host.reg, plan, only_registry)
    assert host.reg.by_name("Working") is None
    assert host.prefix.is_dir()                       # not ticked, not touched
    assert prefixlog.path_for("Working").exists()


def test_everything_ticked_removes_the_prefix_and_the_row(host):
    plan = removal.plan(host.reg, "Working")
    notes = removal.apply(host.reg, plan, plan.chosen())
    assert not host.prefix.exists()
    assert host.reg.by_name("Working") is None
    assert any("Removed" in n for n in notes)


def test_a_running_prefix_removes_nothing_at_all(host, monkeypatch):
    monkeypatch.setattr(removal.probe, "running_pids", lambda *a: [4242])
    plan = removal.plan(host.reg, "Working")
    notes = removal.apply(host.reg, plan, plan.chosen())
    assert host.prefix.is_dir() and host.reg.by_name("Working") is not None
    assert "4242" in notes[0]


def test_what_was_left_alone_is_said_out_loud(host):
    foreign = host.data / "applications" / "Affinity.desktop"
    foreign.write_text("[Desktop Entry]\nName=Affinity\n")
    plan = removal.plan(host.reg, "Working")
    notes = removal.apply(host.reg, plan, plan.chosen())
    assert any("not ours" in n and "Affinity.desktop" in n for n in notes)
    assert foreign.exists()


def test_an_undeletable_item_is_reported_and_the_rest_still_goes(host, monkeypatch):
    """A removal that stopped half way because one file was read-only would be
    worse than one that reports it."""
    plan = removal.plan(host.reg, "Working")
    monkeypatch.setattr(removal.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
    notes = removal.apply(host.reg, plan, plan.chosen())
    assert any("Could not remove" in n for n in notes)
    assert host.reg.by_name("Working") is None        # the rest still happened


# ── snapshots ──────────────────────────────────────────────────────────────

def test_a_snapshot_carries_the_settings_and_the_state_beside_them(host):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix, "before 3.3")
    assert (snap.path / "Settings" / "RecentFiles.xml").is_file()
    assert (snap.path / "sess.db").is_file()
    assert (snap.path / "loose.dat").is_file()
    assert snap.prefix == "Working" and snap.label == "before-3.3"


def test_a_prefix_with_no_settings_says_so(host):
    with pytest.raises(snapshots.NoSettings):
        snapshots.take("Working", host.prefix)


def test_a_label_cannot_escape_the_snapshots_directory(host):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix, "../../etc/passwd")
    assert snapshots.dir_for("Working").resolve() in snap.path.resolve().parents


def test_snapshots_are_listed_newest_first(host, monkeypatch):
    settings_in(host.prefix)
    stamps = iter(["20260101-000000", "20260202-000000", "20260303-000000"])
    monkeypatch.setattr(snapshots, "_stamp", lambda: next(stamps))
    for label in ("first", "second", "third"):
        snapshots.take("Working", host.prefix, label)
    assert [s.label for s in snapshots.listing("Working")] == \
        ["third", "second", "first"]


def test_a_snapshot_with_no_manifest_is_still_a_snapshot(host):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix, "labelled")
    (snap.path / snapshots.MANIFEST).unlink()
    again = snapshots.read_manifest(snap.path)
    assert again.prefix == "unknown" and again.notes
    assert snapshots.listing("Working")          # still listed


# ── restoring ──────────────────────────────────────────────────────────────

def test_restoring_replaces_the_settings_and_keeps_the_old_ones(host):
    settings = settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix, "known-good")
    (settings / "RecentFiles.xml").write_text("<recent>changed since</recent>")

    plan = snapshots.plan_restore(snap, "Working", host.prefix)
    assert plan.crosses_prefixes is False
    notes = snapshots.restore(plan)
    assert (settings / "RecentFiles.xml").read_text() == \
        "<recent>a year of work</recent>"
    assert any("Replaced settings kept at" in n for n in notes)
    assert any("snapshotted as" in n for n in notes)


def test_restoring_into_another_prefix_is_allowed_and_says_so(host):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix, "known-good")

    other = host.tmp / "Test33"
    (other / "drive_c" / "users" / "joshua").mkdir(parents=True)
    (other / "dosdevices").mkdir(parents=True)
    (other / "system.reg").write_text("WINE REGISTRY\n")

    plan = snapshots.plan_restore(snap, "Test33", other)
    assert plan.crosses_prefixes is True
    assert any("was taken from Working" in line for line in plan.describe())
    snapshots.restore(plan, snapshot_first=False)
    assert (plan.destination / "RecentFiles.xml").is_file()


def test_restoring_into_a_running_prefix_is_refused(host, monkeypatch):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix)
    plan = snapshots.plan_restore(snap, "Working", host.prefix)
    monkeypatch.setattr(snapshots.probe if hasattr(snapshots, "probe")
                        else snapshots.maintenance.probe,
                        "running_pids", lambda *a: [999])
    with pytest.raises(snapshots.maintenance.Busy):
        snapshots.restore(plan)


def test_removing_something_outside_the_snapshots_directory_is_refused(host):
    """The manifest is a file on disk, and nothing stops somebody editing it."""
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix)
    snap.path = host.prefix                      # as an edited manifest might
    with pytest.raises(snapshots.NotASnapshot):
        snapshots.remove(snap)
    assert host.prefix.is_dir()


def test_the_stamp_is_shown_as_a_date_a_person_can_read(host):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix, "known-good")
    assert snap.when.count("-") == 2 and ":" in snap.when
    assert snap.when in str(snap) and "known-good" in str(snap)


def test_an_unparseable_stamp_falls_back_to_itself(host):
    settings_in(host.prefix)
    snap = snapshots.take("Working", host.prefix)
    snap.taken = "handmade"
    assert snap.when == "handmade"


def test_a_mime_file_at_our_path_without_our_marker_is_not_removed(host):
    """The path is ours by convention; the marker is what makes the file ours.

    This is the branch the previous test does not reach: a foreign
    Affinity.desktop arrives through foreign_artifacts, never through
    artifacts_for. Something sitting at exactly the filename this prefix would
    use -- hand-written, or left by a version that named it differently -- goes
    through artifacts_for with owner FOREIGN, and must be reported rather than
    offered. Removing it would be this application deleting a file it did not
    write, at a path it guessed."""
    mime = hoststate.mime_path("Working")
    mime.parent.mkdir(parents=True, exist_ok=True)
    mime.write_text("<?xml version='1.0'?>\n<mime-info>not ours</mime-info>\n")
    assert hoststate.mime_owner(mime)[0] == hoststate.FOREIGN

    plan = removal.plan(host.reg, "Working")
    assert all(i.path != mime for i in plan.items), \
        "a file without our marker was offered for removal"
    assert any(a.path == mime for a in plan.left_alone)

    removal.apply(host.reg, plan, plan.chosen())
    assert mime.exists()


def test_a_mime_file_carrying_our_marker_is_removed(host):
    """The other half, so the test above is not passing for the wrong reason."""
    mime = hoststate.mime_path("Working")
    mime.parent.mkdir(parents=True, exist_ok=True)
    mime.write_text(
        f"<?xml version='1.0'?>\n<!-- {hoststate.MIME_MARKER}: Working -->\n"
        "<mime-info/>\n")
    plan = removal.plan(host.reg, "Working")
    assert any(i.path == mime for i in plan.items)
    removal.apply(host.reg, plan, plan.chosen())
    assert not mime.exists()
