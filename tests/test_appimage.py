"""AppImage installs: install, upgrade, going back, and recovering from a cut.

Against a fake image and fake installs built here: a prefix is folders and a
few files, and everything this module does is renames and copies, so nothing
here needs Wine or a real AppImage.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from affinity_manager import appimage, appimage_cli, maintenance, probe, regfile, snapshots  # noqa: E402

FONTS = r"Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts"


def pe(version: str) -> bytes:
    return (b"MZ\0\0" + "ProductVersion".encode("utf-16-le") + b"\0\0"
            + version.encode("utf-16-le") + b"\0\0")


def system_reg(*fonts) -> str:
    lines = ["WINE REGISTRY Version 2", ";; All keys relative to \\\\Machine", "",
             "#arch=win64", "", f"[{FONTS}] 1792441856", "#time=1dc0"]
    lines += [f'"{name}"="{file}"' for name, file in fonts]
    lines += ["", "[Software\\\\Wine] 1", '"x"="y"', ""]
    return "\n".join(lines)


def make_prefix(root: Path, *, affinity="3.3.2.100", nested=True, fonts=(("Arial (TrueType)", "arial.ttf"),)):
    app = root / "drive_c" / "Program Files" / "Affinity"
    if nested:
        app = app / "Affinity"
    app.mkdir(parents=True)
    (app / "Affinity.exe").write_bytes(pe(affinity))
    (app / "libserif.dll").write_text("serif " + affinity)
    (root / "drive_c" / "windows" / "Fonts").mkdir(parents=True)
    (root / "drive_c" / "windows" / "Fonts" / "arial.ttf").write_text("arial")
    (root / "drive_c" / "Program Files" / "Common Files").mkdir(parents=True)
    (root / "drive_c" / "ProgramData").mkdir(parents=True)
    (root / "dosdevices").mkdir()
    (root / "dosdevices" / "c:").symlink_to("../drive_c")
    (root / "dosdevices" / "z:").symlink_to("/")
    (root / "system.reg").write_text(system_reg(*fonts))
    (root / "user.reg").write_text("WINE REGISTRY Version 2\n\n#arch=win64\n")
    return root


def make_image(tmp: Path, name="img", *, version="3.3.2-1", built="2026-11-07T00:00:00+00:00",
               affinity="3.3.2.100", wine="11.21-r6") -> appimage.Image:
    root = tmp / name
    make_prefix(root / "wineprefix", affinity=affinity)
    app = root / "wineprefix" / "drive_c" / "Program Files" / "Affinity" / "Affinity"
    (app / "AffinityPluginLoader.dll").write_text("apl " + version)
    (app / "apl").mkdir()
    (app / "apl" / "winefix.toml").write_text("fix " + version)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "wine").write_text("#!wine " + wine)
    (root / appimage.IMAGE_MANIFEST).write_text(json.dumps({
        "format": 1, "image_version": version, "built": built, "affinity": affinity,
        "wine": wine, "wine_build": f"ElementalWarrior-wine-{wine}",
        "overlay": ["AffinityPluginLoader.dll", "apl"]}))
    return appimage.Image.load(root, tmp / f"Affinity-{version}.AppImage")


def add_user_data(P: Path, home: Path, user="joe"):
    """What a user's own files look like in an install he has used."""
    profile = P / "drive_c" / "users" / user
    roaming = profile / "AppData" / "Roaming" / "Affinity"
    settings = roaming / "Affinity" / "3.0" / "Settings"
    settings.mkdir(parents=True)
    (settings / "RecentFiles.xml").write_text("C:\\a.af")
    (roaming / "Affinity" / "3.0" / "Workspaces").mkdir()
    (roaming / "Affinity" / "3.0" / "Workspaces" / "w.xml").write_text("workspace")
    (roaming / "Affinity" / "3.0" / "sess.db").write_text("signed in")
    (roaming / "Common" / "3.0" / "user").mkdir(parents=True)
    (roaming / "Common" / "3.0" / "user" / "presets.dat").write_text("presets")
    (roaming / "Common" / "3.0" / "modelcache").mkdir()
    (roaming / "Common" / "3.0" / "modelcache" / "model.bin").write_text("m" * 100)
    (profile / "AppData" / "Local" / "Temp").mkdir(parents=True)
    (profile / "AppData" / "Local" / "Temp" / "junk.tmp").write_text("junk")
    docs = home / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "precious.af").write_text("his work")
    (profile / "Documents").symlink_to(docs)
    (P / "drive_c" / "windows" / "Fonts" / "hisfont.ttf").write_text("his font")
    regfile.add_values(P / "system.reg", FONTS, {'"His Font (TrueType)"':
                                                 '"His Font (TrueType)"="hisfont.ttf"\n'})
    (P / "dosdevices" / "w:").symlink_to(str(home))
    (P / "drive_c" / "Program Files" / "SomePlugin").mkdir()
    return profile


@pytest.fixture
def env(tmp_path, monkeypatch):
    from affinity_manager import registry
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path / "Manager")
    home = tmp_path / "home"
    home.mkdir()
    return tmp_path, home


def installed(tmp, home, image, *, kind=appimage.LINKED):
    P = home / ".affinity-appimage-wineprefix"
    appimage.install(appimage.plan_install(image, P, kind))
    return P


# ── install ──────────────────────────────────────────────────────────────────


def test_install_linked(env):
    tmp, home = env
    image = make_image(tmp)
    P = installed(tmp, home, image)
    inst = appimage.inspect(P)
    assert inst.kind == appimage.LINKED and not inst.legacy
    assert inst.image_version == "3.3.2-1"
    assert inst.image_path == tmp / "Affinity-3.3.2-1.AppImage"
    assert (P / appimage.LEGACY_MARKER).is_file()
    assert probe.affinity_version(P) == "3.3.2.100"
    assert not (P.parent / (P.name + appimage.INSTALLING)).exists()
    assert os.readlink(P / "dosdevices" / "c:") == "../drive_c"
    assert not (P / appimage.WINE_LINK).exists()


def test_install_unpacked_is_an_ordinary_prefix(env):
    tmp, home = env
    image = make_image(tmp)
    P = installed(tmp, home, image, kind=appimage.UNPACKED)
    assert os.readlink(P / appimage.WINE_LINK) == "ElementalWarrior-wine-11.21-r6"
    assert (P / appimage.WINE_LINK / "bin" / "wine").is_file()
    assert probe.wine_builds(P) == ["ElementalWarrior-wine-11.21-r6", appimage.WINE_LINK]


def test_install_refuses_a_folder_with_something_in_it(env):
    tmp, home = env
    image = make_image(tmp)
    (home / "busy").mkdir()
    (home / "busy" / "file").write_text("x")
    with pytest.raises(appimage.AppImageError):
        appimage.plan_install(image, home / "busy")
    with pytest.raises(appimage.AppImageError):
        appimage.plan_install(image, home / "x.previous")


def test_install_refuses_without_space(env, monkeypatch):
    tmp, home = env
    image = make_image(tmp)
    monkeypatch.setattr(appimage, "_free", lambda p: 10)
    with pytest.raises(maintenance.NotEnoughSpace):
        appimage.plan_install(image, home / "new")


def test_an_image_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "wineprefix" / "drive_c").mkdir(parents=True)
    with pytest.raises(appimage.NotAnImage):
        appimage.Image.load(tmp_path)


# ── what an install is ───────────────────────────────────────────────────────


def test_a_331_install_is_recognised_and_flat_layout_is_found(env):
    tmp, home = env
    P = make_prefix(home / ".affinity-appimage-wineprefix", affinity="3.3.1.1", nested=False)
    (P / appimage.LEGACY_MARKER).touch()
    inst = appimage.inspect(P)
    assert inst.legacy and inst.kind == appimage.LINKED
    assert probe.has_affinity(P)
    assert probe.affinity_dir(P) == P / probe.FLAT_SUBDIR
    assert inst.affinity == "3.3.1.1"
    assert appimage.compare(make_image(tmp), inst) == "newer"


def test_compare_and_decline(env):
    tmp, home = env
    old = make_image(tmp, "old", version="1", built="2026-11-01T00:00:00+00:00")
    new = make_image(tmp, "new", version="2", built="2026-11-07T00:00:00+00:00")
    P = installed(tmp, home, old)
    inst = appimage.inspect(P)
    assert appimage.compare(new, inst) == "newer"
    assert appimage.compare(old, inst) == "same"
    assert appimage.should_offer(new, inst)
    appimage.decline(P, new)
    assert not appimage.should_offer(new, appimage.inspect(P))
    newer = make_image(tmp, "newer", version="3", built="2026-12-01T00:00:00+00:00")
    assert appimage.should_offer(newer, appimage.inspect(P))
    P2 = home / "second"
    appimage.install(appimage.plan_install(new, P2))
    assert appimage.compare(old, appimage.inspect(P2)) == "older"


def test_remember_image_follows_a_moved_appimage(env):
    tmp, home = env
    P = installed(tmp, home, make_image(tmp))
    appimage.remember_image(P, home / "Applications" / "Affinity.AppImage")
    assert appimage.inspect(P).image_path == home / "Applications" / "Affinity.AppImage"
    assert appimage.inspect(P).image_version == "3.3.2-1"


def test_siblings_are_not_installs(tmp_path):
    assert appimage.sibling_role(tmp_path / "x.previous") == (".previous", tmp_path / "x")
    assert appimage.sibling_role(tmp_path / "x.backups") == (".backups", tmp_path / "x")
    assert appimage.sibling_role(tmp_path / ".previous") is None
    assert appimage.sibling_role(tmp_path / "x") is None


# ── upgrade ──────────────────────────────────────────────────────────────────


def _two(tmp, home):
    old = make_image(tmp, "old", version="3.3.1-1", built="2026-10-01T00:00:00+00:00",
                     affinity="3.3.1.50", wine="11.19")
    new = make_image(tmp, "new", version="3.3.2-1", built="2026-11-07T00:00:00+00:00",
                     affinity="3.3.2.100", wine="11.21-r6")
    P = installed(tmp, home, old)
    profile = add_user_data(P, home)
    return old, new, P, profile


def test_upgrade_keeps_the_old_install_and_carries_the_users_files(env):
    tmp, home = env
    old, new, P, profile = _two(tmp, home)
    plan = appimage.plan_upgrade(new, P)
    assert plan.program == appimage.PROGRAM_IMAGE
    assert plan.extras == ["Program Files/SomePlugin"]
    notes = appimage.upgrade(plan)

    assert probe.affinity_version(P) == "3.3.2.100"
    assert appimage.inspect(P).image_version == "3.3.2-1"
    history = [h["event"] for h in appimage.inspect(P).history]
    assert history == ["installed 3.3.1-1", "upgraded from 3.3.1-1 to 3.3.2-1"]
    prev = appimage.previous_of(P)
    assert probe.affinity_version(prev) == "3.3.1.50"
    assert (prev / "drive_c" / "Program Files" / "SomePlugin").is_dir()

    # The user's own files came across.
    new_profile = P / "drive_c" / "users" / "joe"
    roaming = new_profile / "AppData" / "Roaming" / "Affinity"
    assert (roaming / "Affinity" / "3.0" / "Settings" / "RecentFiles.xml").read_text() == "C:\\a.af"
    assert (roaming / "Affinity" / "3.0" / "sess.db").read_text() == "signed in"
    assert (roaming / "Common" / "3.0" / "user" / "presets.dat").is_file()
    assert not (new_profile / "AppData" / "Local" / "Temp" / "junk.tmp").exists()
    # The models are shared with the kept install, not copied.
    m_new = roaming / "Common" / "3.0" / "modelcache" / "model.bin"
    m_old = prev / "drive_c" / "users" / "joe" / "AppData" / "Roaming" / "Affinity" / \
        "Common" / "3.0" / "modelcache" / "model.bin"
    assert os.stat(m_new).st_ino == os.stat(m_old).st_ino
    # Documents is still a link into his home, and his work is untouched.
    assert (new_profile / "Documents").is_symlink()
    assert (home / "Documents" / "precious.af").read_text() == "his work"
    # Fonts, their registrations and drive letters.
    assert (P / "drive_c" / "windows" / "Fonts" / "hisfont.ttf").is_file()
    assert '"His Font (TrueType)"' in regfile.read_values(P / "system.reg", FONTS)
    assert os.readlink(P / "dosdevices" / "w:") == str(home)
    # A snapshot was taken first, with every part.
    snaps = appimage.list_snapshots(P)
    assert len(snaps) == 1 and snaps[0].auto
    assert set(snaps[0].parts) == {snapshots.PART_COMMON, snapshots.PART_FONTS}
    assert any("SomePlugin" in n for n in notes)
    # Nothing transient is left.
    assert sorted(p.name for p in P.parent.iterdir() if p.name.startswith(P.name)) == \
        [P.name, P.name + ".backups", P.name + ".previous"]


def test_upgrade_without_keeping(env):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P, keep_previous=False))
    assert not appimage.previous_of(P).exists()
    assert (home / "Documents" / "precious.af").read_text() == "his work"
    assert probe.affinity_version(P) == "3.3.2.100"


def test_a_second_upgrade_replaces_the_kept_install(env):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    newest = make_image(tmp, "newest", version="3.3.3-1", built="2026-12-01T00:00:00+00:00",
                        affinity="3.3.3.1")
    plan = appimage.plan_upgrade(newest, P)
    assert plan.replaces_previous is not None
    appimage.upgrade(plan)
    assert probe.affinity_version(appimage.previous_of(P)) == "3.3.2.100"
    assert probe.affinity_version(P) == "3.3.3.1"
    assert not (P.parent / (P.name + appimage.REMOVING)).exists()


def test_a_newer_installed_affinity_is_kept_with_the_images_fixes_over_it(env):
    tmp, home = env
    old = make_image(tmp, "old", version="1", built="2026-10-01T00:00:00+00:00",
                     affinity="3.3.4.9")
    new = make_image(tmp, "new", version="2", built="2026-11-07T00:00:00+00:00",
                     affinity="3.3.2.100")
    P = installed(tmp, home, old)
    plan = appimage.plan_upgrade(new, P)
    assert plan.program == appimage.PROGRAM_KEEP
    appimage.upgrade(plan)
    app = probe.affinity_dir(P)
    assert probe.affinity_version(P) == "3.3.4.9"
    assert (app / "AffinityPluginLoader.dll").read_text() == "apl 2"
    assert (app / "apl" / "winefix.toml").read_text() == "fix 2"


def test_a_331_install_upgrades_into_the_nested_layout(env):
    tmp, home = env
    P = make_prefix(home / ".affinity-appimage-wineprefix", affinity="3.3.1.1", nested=False)
    (P / appimage.LEGACY_MARKER).touch()
    add_user_data(P, home)
    new = make_image(tmp)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    assert probe.affinity_dir(P) == P / probe.AFFINITY_SUBDIR
    assert probe.affinity_version(P) == "3.3.2.100"
    assert appimage.inspect(appimage.previous_of(P)).legacy


def test_going_back_to_an_older_affinity_is_said(env):
    tmp, home = env
    old = make_image(tmp, "old", version="1", built="2026-10-01T00:00:00+00:00",
                     affinity="3.3.4.9")
    new = make_image(tmp, "new", version="2", built="2026-11-07T00:00:00+00:00",
                     affinity="3.3.2.100")
    P = installed(tmp, home, old)
    plan = appimage.plan_upgrade(new, P, program=appimage.PROGRAM_IMAGE)
    assert any("goes back" in n for n in plan.notes)


def test_upgrade_refuses_while_wine_runs(env, monkeypatch):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    plan = appimage.plan_upgrade(new, P)

    class Running:
        state = "running"

        def summary(self):
            return "Affinity is running."
    monkeypatch.setattr(appimage.liveness, "scan", lambda p: Running())
    with pytest.raises(appimage.Busy):
        appimage.upgrade(plan)
    assert not appimage.read_journal(P)


# ── interrupted ──────────────────────────────────────────────────────────────


def _cut_at(monkeypatch, name):
    def boom(*a, **k):
        raise KeyboardInterrupt("power cut")
    monkeypatch.setattr(appimage, name, boom)


def test_cut_while_building_leaves_the_install_as_it_was(env, monkeypatch):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    plan = appimage.plan_upgrade(new, P)
    _cut_at(monkeypatch, "_copy_users")
    with pytest.raises(KeyboardInterrupt):
        appimage.upgrade(plan)
    assert appimage.interrupted(P)
    monkeypatch.undo()
    notes = appimage.resume(P)
    assert "before anything had changed" in notes[0]
    assert probe.affinity_version(P) == "3.3.1.50"
    assert not (P.parent / (P.name + appimage.UPGRADING)).exists()
    assert appimage.interrupted(P) is None


def test_cut_in_the_middle_of_the_swap_is_finished(env, monkeypatch):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    plan = appimage.plan_upgrade(new, P)
    real = Path.rename
    calls = []

    def rename(self, target):
        calls.append(self.name)
        if self.name.endswith(appimage.UPGRADING):
            raise KeyboardInterrupt("power cut")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        appimage.upgrade(plan)
    monkeypatch.undo()
    assert not P.exists()                       # the one dangerous moment
    notes = appimage.resume(P)
    assert any("Finished an upgrade" in n for n in notes)
    assert probe.affinity_version(P) == "3.3.2.100"
    assert probe.affinity_version(appimage.previous_of(P)) == "3.3.1.50"


def test_cut_during_a_switch_is_finished(env, monkeypatch):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    real = Path.rename

    def rename(self, target):
        if self.name.endswith(appimage.SWITCHING):
            raise KeyboardInterrupt
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        appimage.switch_to_previous(P)
    monkeypatch.undo()
    appimage.resume(P)
    assert probe.affinity_version(P) == "3.3.1.50"
    assert probe.affinity_version(appimage.previous_of(P)) == "3.3.2.100"


# ── going back, tidying ──────────────────────────────────────────────────────


def test_switch_twice_is_where_you_started(env):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    appimage.switch_to_previous(P)
    assert probe.affinity_version(P) == "3.3.1.50"
    appimage.switch_to_previous(P)
    assert probe.affinity_version(P) == "3.3.2.100"


def test_remove_previous(env):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    appimage.remove_previous(P)
    assert not appimage.previous_of(P).exists()
    assert (home / "Documents" / "precious.af").read_text() == "his work"
    with pytest.raises(appimage.AppImageError):
        appimage.switch_to_previous(P)


def test_only_transient_names_are_ever_deleted(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x.previous").mkdir()
    with pytest.raises(appimage.AppImageError):
        appimage._remove(tmp_path / "x")
    with pytest.raises(appimage.AppImageError):
        appimage._remove(tmp_path / "x.previous")
    assert (tmp_path / "x").is_dir() and (tmp_path / "x.previous").is_dir()


def test_automatic_snapshots_are_pruned_and_manual_ones_kept(env, monkeypatch):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    stamps = iter(f"20261101-0000{n:02d}" for n in range(30))
    monkeypatch.setattr(snapshots, "_stamp", lambda: next(stamps))
    appimage.take_snapshot(P, "mine")
    for _ in range(7):
        appimage.take_snapshot(P, "auto", auto=True)
    assert appimage.prune_snapshots(P) == 2
    left = appimage.list_snapshots(P)
    assert len([s for s in left if s.auto]) == 5
    assert [s.label for s in left if not s.auto] == ["mine"]


def test_restore_puts_parts_back(env):
    tmp, home = env
    old, new, P, profile = _two(tmp, home)
    snap = appimage.take_snapshot(P, "good")
    common = profile / "AppData" / "Roaming" / "Affinity" / "Common" / "3.0"
    (common / "user" / "presets.dat").write_text("broken")
    (P / "drive_c" / "windows" / "Fonts" / "hisfont.ttf").unlink()
    notes = appimage.restore_snapshot(P, snap.name)
    assert (common / "user" / "presets.dat").read_text() == "presets"
    assert (P / "drive_c" / "windows" / "Fonts" / "hisfont.ttf").is_file()
    held = [p for p in common.parent.iterdir() if ".replaced-" in p.name]
    assert held and (held[0] / "user" / "presets.dat").read_text() == "broken"
    # The models are not in a snapshot, and were left where they were.
    assert (common / "modelcache" / "model.bin").is_file()
    assert any("Common/3.0" in n for n in notes)


def test_manager_snapshots_of_an_appimage_install_go_beside_it(env):
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    snap = snapshots.take("My AppImage", P, "by hand")
    assert snap.path.parent == appimage.backups_of(P)
    assert [s.label for s in snapshots.listing("My AppImage", P)] == ["by-hand"]
    snapshots.remove(snap)
    assert snapshots.listing("My AppImage", P) == []


# ── registry files ───────────────────────────────────────────────────────────


def test_regfile_adds_only_what_is_missing(tmp_path):
    reg = tmp_path / "system.reg"
    reg.write_text(system_reg(("Arial (TrueType)", "arial.ttf")))
    added = regfile.add_values(reg, FONTS, {
        '"Arial (TrueType)"': '"Arial (TrueType)"="other.ttf"\n',
        '"New (TrueType)"': '"New (TrueType)"="new.ttf"\n'})
    assert added == ['"New (TrueType)"']
    values = regfile.read_values(reg, FONTS)
    assert values['"Arial (TrueType)"'] == '"Arial (TrueType)"="arial.ttf"\n'
    assert values['"New (TrueType)"'] == '"New (TrueType)"="new.ttf"\n'
    # The next section is untouched, and still separated by a blank line.
    text = reg.read_text()
    assert '"New (TrueType)"="new.ttf"\n\n[Software\\\\Wine] 1' in text


def test_regfile_makes_a_missing_section_and_keeps_continuations(tmp_path):
    reg = tmp_path / "user.reg"
    reg.write_text("WINE REGISTRY Version 2\n\n#arch=win64\n")
    entry = '"Wrapped"=hex:00,01,\\\n  02,03\n'
    regfile.add_values(reg, FONTS, {'"Wrapped"': entry})
    assert regfile.read_values(reg, FONTS) == {'"Wrapped"': entry}
    assert regfile._value_name('"a \\" b"="x"') == '"a \\" b"'


# ── the command line ─────────────────────────────────────────────────────────


def test_cli_status_install_and_upgrade(env, capsys):
    tmp, home = env
    old = make_image(tmp, "old", version="1", built="2026-10-01T00:00:00+00:00")
    new = make_image(tmp, "new", version="2", built="2026-11-07T00:00:00+00:00")
    P = home / "Custom"
    assert appimage_cli.main(["--prefix", str(P), "--image-root", str(old.root),
                              "--json", "install"]) == 0
    assert appimage_cli.main(["--prefix", str(P), "--image-root", str(new.root),
                              "--json", "status"]) == 0
    out = capsys.readouterr().out
    status = json.loads(out.strip().splitlines()[-1][len("RESULT "):])
    assert status["relation"] == "newer"
    assert appimage_cli.main(["--prefix", str(P), "--image-root", str(new.root),
                              "--json", "upgrade", "--discard-previous"]) == 0
    out = capsys.readouterr().out
    assert "PROGRESS" in out
    assert appimage.inspect(P).image_version == "2"
    assert not appimage.previous_of(P).exists()


def test_cli_prepare_installs_then_offers_then_launches(env, monkeypatch):
    tmp, home = env
    old = make_image(tmp, "old", version="1", built="2026-10-01T00:00:00+00:00")
    new = make_image(tmp, "new", version="2", built="2026-11-07T00:00:00+00:00")
    P = home / ".affinity-appimage-wineprefix"
    answers = []
    monkeypatch.setattr(appimage_cli.Ui, "choose",
                        lambda self, text, options, default=0, title="": answers.pop(0))
    monkeypatch.setattr(appimage_cli.Ui, "info", lambda self, *a, **k: None)
    args = ["--prefix", str(P), "--image-root"]
    assert appimage_cli.main(args + [str(old.root), "prepare"]) == appimage_cli.EXIT_LAUNCH
    assert appimage.inspect(P).image_version == "1"
    answers.append(2)                                     # not now
    assert appimage_cli.main(args + [str(new.root), "prepare"]) == appimage_cli.EXIT_LAUNCH
    assert appimage.inspect(P).image_version == "1"
    assert appimage_cli.main(args + [str(new.root), "prepare"]) == appimage_cli.EXIT_LAUNCH
    assert answers == []                                  # not asked again
    newest = make_image(tmp, "newest", version="3", built="2026-12-01T00:00:00+00:00")
    answers.append(0)                                     # upgrade, keep
    assert appimage_cli.main(args + [str(newest.root), "prepare"]) == appimage_cli.EXIT_LAUNCH
    assert appimage.inspect(P).image_version == "3"
    assert appimage.previous_of(P).exists()
    answers.append(1)                                     # older image: cancel
    assert appimage_cli.main(args + [str(old.root), "prepare"]) == appimage_cli.EXIT_STOP


def test_cli_prepare_leaves_a_foreign_folder_alone(env, monkeypatch):
    tmp, home = env
    image = make_image(tmp)
    P = home / "someones-prefix"
    make_prefix(P)
    monkeypatch.setattr(appimage_cli.Ui, "info", lambda self, *a, **k: None)
    assert appimage_cli.main(["--prefix", str(P), "--image-root", str(image.root),
                              "prepare"]) == appimage_cli.EXIT_STOP
    assert not appimage.is_install(P)


# ── backup levels ────────────────────────────────────────────────────────────


def test_backup_levels_and_restoring_them_into_a_rebuilt_prefix(env, monkeypatch):
    from affinity_manager import backups
    monkeypatch.setattr(backups, "filesystem_type", lambda path: "ext4")
    tmp, home = env
    old, new, P, profile = _two(tmp, home)
    where = tmp / "Backups"
    prog = backups.create(backups.plan_backup("AppImage", P, where, level=backups.LEVEL_PROGRAM))
    sett = backups.create(backups.plan_backup("AppImage", P, where, label="s",
                                              level=backups.LEVEL_SETTINGS))
    assert prog.level == backups.LEVEL_PROGRAM and sett.level == backups.LEVEL_SETTINGS
    assert backups.verify(prog) == [] and backups.verify(sett) == []
    assert (prog.path / "program" / "Affinity.exe").is_file()
    assert not (sett.path / "program").exists()
    assert (sett.path / "settings" / "Settings" / "RecentFiles.xml").is_file()
    assert prog.bytes < maintenance._du(P)

    # The install is rebuilt from the newer image, losing his settings and
    # his Affinity; the program backup puts both back.
    appimage.upgrade(appimage.plan_upgrade(new, P, keep_previous=False))
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(P / "drive_c" / "users")
    plan = backups.plan_restore(prog)
    assert plan.program_aside is not None
    notes = backups.restore(plan)
    assert probe.affinity_version(P) == "3.3.1.50"
    assert plan.program_aside.is_dir()            # the replaced one is kept
    # A prefix Wine has not run in yet has no profile; the settings go to the
    # login user's, which is the one Wine will create.
    import getpass
    restored = P / "drive_c" / "users" / getpass.getuser() / "AppData" / "Roaming" / "Affinity"
    assert (restored / "Affinity" / "3.0" / "Settings" / "RecentFiles.xml").is_file()
    assert (restored / "Common" / "3.0" / "user" / "presets.dat").is_file()
    assert notes


def test_a_partial_backup_needs_a_prefix_to_restore_into(env, monkeypatch):
    from affinity_manager import backups
    monkeypatch.setattr(backups, "filesystem_type", lambda path: "ext4")
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    b = backups.create(backups.plan_backup("AppImage", P, tmp / "Backups",
                                           level=backups.LEVEL_SETTINGS))
    with pytest.raises(backups.BackupError):
        backups.plan_restore(b, target=tmp / "nowhere")


def test_discovery_skips_an_installs_own_siblings(env, monkeypatch):
    from affinity_manager import discover
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    monkeypatch.setattr(discover, "search_roots", lambda: [home])
    monkeypatch.setattr(discover, "remembered_paths", lambda: [])
    found = discover.find_installations()
    assert [Path(r["path"]).name for r in found] == [P.name]
    assert found[0]["appimage"] == appimage.LINKED


def test_removal_offers_the_previous_install_with_the_prefix(env):
    from affinity_manager import registry, removal
    tmp, home = env
    old, new, P, _ = _two(tmp, home)
    appimage.upgrade(appimage.plan_upgrade(new, P))
    reg = registry.Registry(tmp / "Manager" / "prefixes.json")
    reg.add("AppImage", P)
    plan = removal.plan(reg, "AppImage")
    kinds = {i.kind: i for i in plan.items}
    assert kinds["appimage-previous"].path == appimage.previous_of(P)
    assert kinds["settings-backup"].path == appimage.backups_of(P)
    assert not kinds["settings-backup"].default
    removal.apply(reg, plan, plan.chosen())
    assert not P.exists() and not appimage.previous_of(P).exists()
    assert appimage.backups_of(P).is_dir()                 # unticked, so kept
    assert (home / "Documents" / "precious.af").read_text() == "his work"


def test_linked_installs_launch_through_their_appimage(env):
    from affinity_manager import appimagerun, commands, defaultentry
    tmp, home = env
    image = make_image(tmp)
    image.path.write_text("#!/bin/sh\n")
    P = home / "Elsewhere"
    appimage.install(appimage.plan_install(image, P))
    cmd = appimagerun.launch_command(P)
    assert cmd.argv == [str(image.path)]
    assert cmd.env[appimage.ENV_PREFIX] == str(P)
    assert cmd.env["APPIMAGELAUNCHER_DISABLE"] == "1"
    menu, takes = defaultentry.launch_command(P)
    assert menu.argv == [str(image.path)] and takes
    assert any(c.argv[1:3] == ["--wine", "winecfg"] for c in commands.for_prefix(P))
    image.path.unlink()
    assert "not where it was" in appimagerun.can_launch(appimage.inspect(P))


def test_a_331_install_elsewhere_cannot_be_launched_by_its_appimage(env):
    from affinity_manager import appimagerun
    tmp, home = env
    P = make_prefix(home / "copy", affinity="3.3.1.1", nested=False)
    (P / appimage.LEGACY_MARKER).touch()
    img = tmp / "old.AppImage"
    img.write_text("x")
    appimage.remember_image(P, img)
    assert appimage.inspect(P).image_version == "3.3.1 or earlier"
    assert "only run ~/.affinity-appimage-wineprefix" in appimagerun.can_launch(appimage.inspect(P))


def test_only_font_registrations_naming_a_real_file_are_carried(tmp_path):
    P = make_prefix(tmp_path / "p")
    (P / "drive_c" / "windows" / "Fonts" / "mine.ttf").write_text("x")
    host = tmp_path / "hostfont.ttf"
    host.write_text("x")
    z = "Z:" + str(host).replace("/", "\\\\")
    regs = {"system.reg": {
        '"Mine (TrueType)"': '"Mine (TrueType)"="mine.ttf"\n',
        '"Gone (TrueType)"': '"Gone (TrueType)"="gone.ttf"\n',
        '"Host (TrueType)"': f'"Host (TrueType)"="{z}"\n',
        '"Builder (TrueType)"': '"Builder (TrueType)"="Z:\\\\home\\\\matt\\\\x.ttf"\n',
        '"Yahei (TrueType)"': '"Yahei (TrueType)"="Z:\\\\usr\\\\share\\\\wine\\\\fonts\\\\msyh.ttf"\n',
    }}
    regfile.add_font_registrations(P, regs)
    have = set(regfile.read_values(P / "system.reg", FONTS))
    assert {'"Mine (TrueType)"', '"Host (TrueType)"'} <= have
    assert not have & {'"Gone (TrueType)"', '"Builder (TrueType)"', '"Yahei (TrueType)"'}
