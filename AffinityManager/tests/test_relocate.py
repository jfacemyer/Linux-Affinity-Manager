"""Moving a prefix, and making the launchers follow it.

The substitution is the part with teeth. ~/.AffinityLinux is a prefix of
~/.AffinityLinuxManager and of ~/.AffinityLinux-winetest, so a plain string
replace would break two other things while fixing one -- and one of those two
is this application's own metadata directory.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from affinity_manager import maintenance, registry  # noqa: E402


def make_prefix(path: Path) -> Path:
    (path / "drive_c" / "windows").mkdir(parents=True)
    (path / "dosdevices").mkdir(parents=True)
    (path / "dosdevices" / "c:").symlink_to("../drive_c")
    (path / "system.reg").write_text("WINE REGISTRY\n")
    (path / "drive_c" / "windows" / "notepad.exe").write_text("x" * 64)
    return path


@pytest.fixture
def prefix(tmp_path):
    return make_prefix(tmp_path / "AffinityLinux")


# -- planning ---------------------------------------------------------------

def test_a_move_within_one_filesystem_is_a_rename(prefix, tmp_path):
    plan = maintenance.plan_relocate(prefix, tmp_path / "Managed" / "Working")
    assert plan.same_filesystem and plan.instant
    assert plan.size > 0


def test_a_destination_inside_the_source_is_refused(prefix):
    with pytest.raises(maintenance.DestinationInUse):
        maintenance.plan_relocate(prefix, prefix / "inside")


def test_a_destination_with_something_in_it_is_refused(prefix, tmp_path):
    busy = tmp_path / "Busy"
    busy.mkdir()
    (busy / "something").write_text("already here")
    with pytest.raises(maintenance.DestinationInUse):
        maintenance.plan_relocate(prefix, busy)


def test_something_that_is_not_a_prefix_is_refused(tmp_path):
    plain = tmp_path / "Documents"
    plain.mkdir()
    with pytest.raises(maintenance.NotAPrefix):
        maintenance.plan_relocate(plain, tmp_path / "Elsewhere")


# -- moving -----------------------------------------------------------------

def test_the_prefix_arrives_and_the_old_path_is_gone(prefix, tmp_path):
    dest = tmp_path / "Managed" / "Working"
    plan = maintenance.plan_relocate(prefix, dest)
    maintenance.relocate(plan)
    assert (dest / "drive_c" / "windows" / "notepad.exe").is_file()
    assert not prefix.exists()


def test_relative_links_survive_the_move(prefix, tmp_path):
    dest = tmp_path / "Managed" / "Working"
    maintenance.relocate(maintenance.plan_relocate(prefix, dest))
    link = dest / "dosdevices" / "c:"
    assert link.is_symlink() and os.readlink(link) == "../drive_c"
    assert (link / "windows").is_dir()


def test_an_absolute_link_into_the_old_path_is_repointed(tmp_path):
    """A rename does not rewrite symlinks, and the installer has been seen
    writing ElementalWarriorWine absolute. Left alone it would run Wine out of
    a directory that no longer exists."""
    prefix = make_prefix(tmp_path / "AffinityLinux")
    build = prefix / "ElementalWarrior-wine-11.16" / "bin"
    build.mkdir(parents=True)
    (build / "wine").write_text("#!/bin/sh\n")
    (prefix / "ElementalWarriorWine").symlink_to(
        str(prefix / "ElementalWarrior-wine-11.16"))

    dest = tmp_path / "Managed" / "Working"
    maintenance.relocate(maintenance.plan_relocate(prefix, dest))
    target = os.readlink(dest / "ElementalWarriorWine")
    assert target == str(dest / "ElementalWarrior-wine-11.16")
    assert (dest / "ElementalWarriorWine" / "bin" / "wine").is_file()


# -- the launchers ----------------------------------------------------------

@pytest.fixture
def launcher_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    return d


def test_a_launcher_follows_the_prefix(tmp_path, launcher_dir):
    old, new = tmp_path / ".AffinityLinux", tmp_path / "Managed" / "Working"
    script = launcher_dir / "affinity"
    script.write_text(
        '#!/bin/sh\nPFX="%s"\nAPP="%s/drive_c/Program Files"\nexec "$PFX/bin/wine"\n'
        % (old, old))
    script.chmod(0o755)

    changed = maintenance.repoint_launchers(old, new, dirs=[launcher_dir])
    assert len(changed) == 1 and "2 reference" in changed[0]
    text = script.read_text()
    assert str(new) in text and str(old) not in text
    assert oct(script.stat().st_mode)[-3:] == "755"


def test_a_longer_path_that_merely_starts_the_same_is_left_alone(tmp_path, launcher_dir):
    """The two that would break: this application's own metadata directory,
    and the separate test prefix."""
    old = tmp_path / ".AffinityLinux"
    script = launcher_dir / "affinity"
    script.write_text(
        "META=%s/prefixes.json\n"
        "TEST=%s-winetest\n"
        "PFX=%s\n" % (tmp_path / ".AffinityLinuxManager", old, old))
    maintenance.repoint_launchers(old, tmp_path / "Moved", dirs=[launcher_dir])
    text = script.read_text()
    assert str(tmp_path / ".AffinityLinuxManager") + "/prefixes.json" in text
    assert str(old) + "-winetest" in text
    assert "PFX=%s\n" % (tmp_path / "Moved") in text


def test_the_original_is_kept_beside_the_launcher(tmp_path, launcher_dir):
    old = tmp_path / ".AffinityLinux"
    script = launcher_dir / "affinity-33.sh"
    original = 'PFX="%s"\n' % old
    script.write_text(original)
    maintenance.repoint_launchers(old, tmp_path / "Moved", dirs=[launcher_dir])
    backups = [f for f in launcher_dir.iterdir() if f.name.endswith(".before-move")]
    assert len(backups) == 1 and backups[0].read_text() == original


def test_a_dry_run_changes_nothing(tmp_path, launcher_dir):
    old = tmp_path / ".AffinityLinux"
    script = launcher_dir / "affinity"
    script.write_text('PFX="%s"\n' % old)
    report = maintenance.repoint_launchers(old, tmp_path / "Moved",
                                           dirs=[launcher_dir], dry_run=True)
    assert report and "would be updated" in report[0]
    assert str(old) in script.read_text()
    assert not any(f.name.endswith(".before-move") for f in launcher_dir.iterdir())


def test_a_launcher_that_does_not_name_the_prefix_is_not_touched(tmp_path, launcher_dir):
    old = tmp_path / ".AffinityLinux"
    other = launcher_dir / "unrelated"
    other.write_text("echo hello\n")
    assert maintenance.repoint_launchers(old, tmp_path / "Moved",
                                         dirs=[launcher_dir]) == []
    assert other.read_text() == "echo hello\n"


def test_a_binary_in_the_launcher_directory_is_skipped(tmp_path, launcher_dir):
    """~/bin here holds 107MB of binaries. Reading them all cost 3.98s once."""
    old = tmp_path / ".AffinityLinux"
    blob = launcher_dir / "some-binary"
    blob.write_bytes(b"\x00\x01\x02" + str(old).encode() + b"\xff\xfe")
    assert maintenance.launchers_naming(old, dirs=[launcher_dir]) == []


# -- the registry follows too ------------------------------------------------

def test_the_registry_can_be_repointed(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path)
    reg = registry.Registry(tmp_path / "prefixes.json")
    reg.add("Working", str(tmp_path / "old"))
    entry = reg.repoint("Working", tmp_path / "new")
    assert entry["path"] == str(tmp_path / "new") and entry["moved"]
    assert registry.Registry(tmp_path / "prefixes.json").by_name("Working")["path"] \
        == str(tmp_path / "new")


def test_repointing_onto_another_prefixs_path_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path)
    reg = registry.Registry(tmp_path / "prefixes.json")
    reg.add("Working", str(tmp_path / "one"))
    reg.add("Test", str(tmp_path / "two"))
    with pytest.raises(registry.PathInUse):
        reg.repoint("Test", tmp_path / "one")
    assert reg.by_name("Test")["path"] == str(tmp_path / "two")
