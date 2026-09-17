"""The first run, and carrying settings between prefixes.

Two things are being protected here. That an existing ~/.AffinityLinux is
never quietly taken over or quietly moved -- both are offered, separately, and
the explanation is built from the same facts the action uses so it cannot
drift. And that copying settings into a prefix never destroys what is there
without leaving it recoverable: RecentFiles.xml is the one thing a reinstall
cannot give back.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from affinity_manager import coldstart, discover, prefsseed, registry  # noqa: E402


def make_prefix(path: Path, *, affinity=True) -> Path:
    (path / "drive_c" / "windows").mkdir(parents=True)
    (path / "dosdevices").mkdir(parents=True)
    (path / "dosdevices" / "c:").symlink_to("../drive_c")
    (path / "system.reg").write_text("WINE REGISTRY\n")
    if affinity:
        app = path / "drive_c" / "Program Files" / "Affinity" / "Affinity"
        app.mkdir(parents=True)
        (app / "Affinity.exe").write_text("MZ" + "x" * 400)
    return path


def make_settings(prefix: Path, user="joshua", version="3.0", *, recents=True) -> Path:
    root = prefix / "drive_c" / "users" / user
    settings = root.joinpath(*prefsseed.APPDATA_TAIL) / version / "Settings"
    settings.mkdir(parents=True)
    (settings / "preferences.dat").write_text("mine")
    (settings / "shortcuts").mkdir()
    (settings / "shortcuts" / "keys.xml").write_text("<keys/>")
    if recents:
        (settings / "RecentFiles.xml").write_text("<recent>the year of work</recent>")
    return settings


# ── which situation is this ─────────────────────────────────────────────────

@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path / "meta")
    monkeypatch.setattr(registry, "base_dir", lambda: tmp_path / "base")
    monkeypatch.setattr(registry, "base_dir_is_configured", lambda: False)
    (tmp_path / "meta").mkdir()
    return tmp_path


def test_a_list_with_entries_asks_nothing(clean_home):
    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    reg.entries = [{"name": "Working", "path": str(clean_home / "Working")}]
    situation = coldstart.look(reg, search=False)
    assert situation.state == coldstart.MANAGED
    assert situation.asks_anything is False


def test_nothing_found_and_no_list_is_a_fresh_start(clean_home, monkeypatch):
    monkeypatch.setattr(discover, "find_installations", lambda *a, **k: [])
    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    assert coldstart.look(reg).state == coldstart.FRESH


def test_an_unmanaged_install_makes_it_adoptable(clean_home, monkeypatch):
    found = [{"path": str(clean_home / ".AffinityLinux"), "managed_as": None,
              "suggested_name": "AffinityLinux"}]
    monkeypatch.setattr(discover, "find_installations", lambda *a, **k: found)
    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    situation = coldstart.look(reg)
    assert situation.state == coldstart.ADOPTABLE
    assert situation.installs == found


def test_an_install_already_managed_is_not_offered_for_adoption(clean_home, monkeypatch):
    """Belt and braces: with an empty list there should be none, but the search
    reads the registry itself and a hand-edited file should not produce an offer
    to adopt something already held."""
    monkeypatch.setattr(discover, "find_installations", lambda *a, **k: [
        {"path": str(clean_home / ".AffinityLinux"), "managed_as": "Working"}])
    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    assert coldstart.look(reg).state == coldstart.FRESH


# ── what each choice says it will do ────────────────────────────────────────

def test_managing_in_place_promises_to_touch_nothing(clean_home):
    prefix = make_prefix(clean_home / ".AffinityLinux")
    lines = " ".join(coldstart.explain_in_place({"path": str(prefix)}))
    assert str(prefix) in lines
    assert "stays exactly where it is" in lines
    assert "keeps working" in lines


def test_a_move_on_one_filesystem_says_it_copies_nothing(clean_home):
    prefix = make_prefix(clean_home / ".AffinityLinux")
    lines = " ".join(coldstart.explain_move({"path": str(prefix)}, "Working",
                                            base=clean_home / "base"))
    assert "rename" in lines and "nothing is copied" in lines


def test_a_move_names_the_launchers_it_would_rewrite(clean_home, monkeypatch):
    prefix = make_prefix(clean_home / ".AffinityLinux")
    bins = clean_home / "bin"
    bins.mkdir()
    (bins / "affinity-gpu").write_text('PFX="%s"\n' % prefix)
    monkeypatch.setattr(coldstart.maintenance, "LAUNCHER_DIRS", (str(bins),))
    lines = " ".join(coldstart.explain_move({"path": str(prefix)}, "Working",
                                            base=clean_home / "base"))
    assert "affinity-gpu" in lines and "kept beside it" in lines


def test_a_move_says_so_when_there_is_nothing_to_rewrite(clean_home, monkeypatch):
    prefix = make_prefix(clean_home / ".AffinityLinux")
    empty = clean_home / "nothing"
    empty.mkdir()
    monkeypatch.setattr(coldstart.maintenance, "LAUNCHER_DIRS", (str(empty),))
    lines = " ".join(coldstart.explain_move({"path": str(prefix)}, "Working",
                                            base=clean_home / "base"))
    assert "nothing to update" in lines


def test_an_impossible_move_explains_itself_rather_than_raising(clean_home):
    plain = clean_home / "Documents"
    plain.mkdir()
    lines = " ".join(coldstart.explain_move({"path": str(plain)}, "Working",
                                            base=clean_home / "base"))
    assert "cannot be done" in lines


# ── acting ──────────────────────────────────────────────────────────────────

def test_managing_in_place_leaves_the_prefix_alone(clean_home):
    prefix = make_prefix(clean_home / ".AffinityLinux")
    before = sorted(p.name for p in prefix.iterdir())
    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    entry = coldstart.adopt_in_place(reg, {"path": str(prefix)}, "Working")
    assert entry["path"] == str(prefix)
    assert sorted(p.name for p in prefix.iterdir()) == before


def test_moving_records_the_new_path_and_repoints_the_launcher(clean_home, monkeypatch):
    prefix = make_prefix(clean_home / ".AffinityLinux")
    bins = clean_home / "bin"
    bins.mkdir()
    launcher = bins / "affinity-gpu"
    launcher.write_text('PFX="%s"\nexec "$PFX/bin/wine"\n' % prefix)
    monkeypatch.setattr(coldstart.maintenance, "LAUNCHER_DIRS", (str(bins),))

    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    entry, notes = coldstart.adopt_by_moving(reg, {"path": str(prefix)}, "Working",
                                             base=clean_home / "base")
    dest = clean_home / "base" / registry.dir_name("Working")
    assert entry["path"] == str(dest)
    assert (dest / "system.reg").is_file() and not prefix.exists()
    assert str(dest) in launcher.read_text()
    assert notes


def test_a_failed_move_writes_nothing_to_the_list(clean_home):
    """The registry entry comes last on purpose: one pointing at a move that
    did not happen is worse than no entry at all."""
    plain = clean_home / "Documents"
    plain.mkdir()
    reg = registry.Registry(clean_home / "meta" / "prefixes.json")
    with pytest.raises(coldstart.maintenance.NotAPrefix):
        coldstart.adopt_by_moving(reg, {"path": str(plain)}, "Working",
                                  base=clean_home / "base")
    assert reg.by_name("Working") is None


# ── settings, carried over ──────────────────────────────────────────────────

def test_settings_are_found_whatever_the_user_is_called(tmp_path):
    prefix = make_prefix(tmp_path / "Old")
    make_settings(prefix, user="someone-else")
    found = prefsseed.settings_in(prefix)
    assert len(found) == 1 and found[0].version == "3.0"
    assert found[0].has_recents and found[0].files == 3


def test_the_destination_prefix_is_not_offered_as_a_source(tmp_path):
    a, b = make_prefix(tmp_path / "A"), make_prefix(tmp_path / "B")
    make_settings(a)
    make_settings(b)
    offered = prefsseed.sources([a, b], exclude=b)
    assert [s.prefix for s in offered] == [a]


def test_a_source_can_be_given_as_a_prefix_a_version_or_the_settings_itself(tmp_path):
    prefix = make_prefix(tmp_path / "Old")
    settings = make_settings(prefix)
    for given in (prefix, settings.parent, settings):
        resolved = prefsseed.resolve_source(given)
        assert resolved is not None and resolved.path == settings


def test_somewhere_with_no_settings_resolves_to_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert prefsseed.resolve_source(empty) is None


def test_replacing_saves_the_old_settings_aside_rather_than_deleting_them(tmp_path):
    """RecentFiles.xml is the one thing a reinstall cannot give back."""
    old = make_prefix(tmp_path / "Old")
    new = make_prefix(tmp_path / "New")
    make_settings(old)
    source = prefsseed.settings_in(old)[0]
    stock = make_settings(new, recents=False)
    (stock / "preferences.dat").write_text("stock")

    result = prefsseed.seed(source, stock, mode=prefsseed.REPLACE)
    assert result.saved_aside is not None and result.saved_aside.is_dir()
    assert (result.saved_aside / "preferences.dat").read_text() == "stock"
    assert (stock / "preferences.dat").read_text() == "mine"
    assert (stock / "RecentFiles.xml").is_file()
    assert (stock / "shortcuts" / "keys.xml").is_file()


def test_filling_keeps_what_is_already_there(tmp_path):
    """The installer's rule, and the reason for it: the destination's own
    preferences and RecentFiles.xml survive."""
    old, new = make_prefix(tmp_path / "Old"), make_prefix(tmp_path / "New")
    make_settings(old)
    source = prefsseed.settings_in(old)[0]
    theirs = make_settings(new)
    (theirs / "preferences.dat").write_text("theirs")
    (theirs / "RecentFiles.xml").write_text("<recent>their own</recent>")
    (source.path / "extra.dat").write_text("only in the source")

    result = prefsseed.seed(source, theirs, mode=prefsseed.FILL)
    assert (theirs / "preferences.dat").read_text() == "theirs"
    assert (theirs / "RecentFiles.xml").read_text() == "<recent>their own</recent>"
    assert (theirs / "extra.dat").read_text() == "only in the source"
    assert result.saved_aside is None
    assert Path("preferences.dat") in result.kept


def test_the_plan_says_what_would_be_replaced_without_writing(tmp_path):
    old, new = make_prefix(tmp_path / "Old"), make_prefix(tmp_path / "New")
    make_settings(old)
    source = prefsseed.settings_in(old)[0]
    theirs = make_settings(new, recents=False)

    before = sorted(p.name for p in theirs.rglob("*"))
    result = prefsseed.plan(source, theirs)
    assert Path("RecentFiles.xml") in result.added
    assert Path("preferences.dat") in result.replaced
    assert sorted(p.name for p in theirs.rglob("*")) == before


def test_a_prefix_with_no_settings_yet_gets_a_sensible_destination(tmp_path):
    new = make_prefix(tmp_path / "New")
    (new / "drive_c" / "users" / "joshua").mkdir(parents=True)
    dest = prefsseed.destination_for(new)
    assert dest.name == "Settings" and dest.parent.name == "3.0"
    assert "joshua" in str(dest)
    assert not dest.exists()          # looking must not create it


def test_an_existing_settings_directory_wins_over_a_guess(tmp_path):
    new = make_prefix(tmp_path / "New")
    settings = make_settings(new, user="someone-else", version="2.0")
    assert prefsseed.destination_for(new) == settings


# ── the search reports everything, and says what each thing is ──────────────

def test_a_prefix_with_no_affinity_is_still_found(tmp_path, monkeypatch):
    """An interrupted install has drive_c, dosdevices and nothing else. It was
    the one case the old filter hid, and the one where somebody most needs the
    manager to admit it can see it."""
    monkeypatch.setattr(discover, "search_roots", lambda: [tmp_path])
    monkeypatch.setattr(discover, "remembered_paths", lambda: [])
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path / "meta")
    (tmp_path / "meta").mkdir()
    make_prefix(tmp_path / "Half", affinity=False)
    make_prefix(tmp_path / "Whole")

    found = {Path(r["path"]).name: r for r in discover.find_installations()}
    assert set(found) == {"Half", "Whole"}
    assert found["Whole"]["has_affinity"] is True
    assert found["Half"]["has_affinity"] is False


def test_the_search_does_not_descend_into_a_prefix(tmp_path, monkeypatch):
    """drive_c is enormous and holds nothing that is itself a prefix."""
    monkeypatch.setattr(discover, "search_roots", lambda: [tmp_path])
    monkeypatch.setattr(discover, "remembered_paths", lambda: [])
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path / "meta")
    (tmp_path / "meta").mkdir()
    outer = make_prefix(tmp_path / "Outer")
    make_prefix(outer / "drive_c" / "Nested")

    found = [Path(r["path"]).name for r in discover.find_installations()]
    assert found == ["Outer"]


def test_the_affinity_column_says_what_an_incomplete_prefix_is():
    """Read off the dialog's own helper, so the wording cannot drift from the
    three cases it has to cover."""
    import importlib
    app = importlib.import_module("AffinityLinuxManager")
    what = app.FindDialog._what_it_is
    assert what({"has_affinity": True, "affinity_version": "3.3.1"}) == "3.3.1"
    assert what({"has_affinity": True, "affinity_version": None}) == "installed"
    assert what({"has_affinity": False, "wine": "ElementalWarrior-wine-11.16"}) \
        == "not installed yet"
    assert what({"has_affinity": False, "wine": None}) == "empty prefix"
