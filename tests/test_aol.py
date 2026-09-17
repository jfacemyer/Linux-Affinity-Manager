"""Loading the installer instead of copying it.

The contract this rests on: the installer file stays byte-identical upstream,
and the manager reaches into it by name. So the test that matters is not "does
it load" but "are the names still there" -- because a rename upstream is
silent, and the symptom without this is a blank window or an AttributeError an
hour into a session.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from affinity_manager import aol, ui  # noqa: E402


def installer_available() -> bool:
    try:
        aol.module()
        return True
    except aol.NotAvailable:
        return False


needs_installer = pytest.mark.skipif(
    not installer_available(), reason="no AffinityOnLinux checkout to load")


@needs_installer
def test_every_name_the_manager_reaches_for_is_present():
    """If this fails, upstream renamed something and the message says which."""
    assert aol.verify() == []


@needs_installer
def test_loading_is_cached():
    assert aol.module() is aol.module()


@needs_installer
def test_loading_starts_no_gui_and_no_threads():
    """It is import-safe because everything at column zero is a def, a class or
    the __main__ guard -- but that is upstream's file, so check rather than
    trust it."""
    import threading
    before = threading.active_count()
    aol.module()
    assert threading.active_count() == before
    assert "QApplication" not in repr(getattr(aol.module(), "app", None))


@needs_installer
def test_all_three_themes_still_produce_a_stylesheet():
    for theme in ui.THEMES:
        css = ui.stylesheet(theme)
        assert len(css) > 1000, f"{theme} produced {len(css)} characters"
        assert "QPushButton" in css


@needs_installer
def test_the_widgets_resolve_to_classes():
    assert isinstance(ui.log_widget_class(), type)
    assert isinstance(ui.spinner_class(), type)


@needs_installer
def test_target_env_sets_and_restores():
    key = aol.module().ENV_INSTALL_DIR
    os.environ[key] = "/was/here"
    try:
        with aol.target_env("/tmp/somewhere"):
            assert os.environ[key] == "/tmp/somewhere"
        assert os.environ[key] == "/was/here"
    finally:
        os.environ.pop(key, None)


@needs_installer
def test_target_env_restores_even_when_the_block_raises():
    """The installer is constructed inside this block. If construction throws,
    a leaked AFFINITY_INSTALL_DIR would redirect every later subprocess."""
    key = aol.module().ENV_INSTALL_DIR
    os.environ.pop(key, None)
    with pytest.raises(RuntimeError):
        with aol.target_env("/tmp/somewhere"):
            raise RuntimeError("construction failed")
    assert key not in os.environ


@needs_installer
def test_target_env_clears_a_stale_installer_file():
    """An installer file left from a previous prefix must not be inherited by
    the next one."""
    mod = aol.module()
    os.environ[mod.ENV_INSTALLER_FILE] = "/old/Affinity.exe"
    try:
        with aol.target_env("/tmp/p"):
            assert mod.ENV_INSTALLER_FILE not in os.environ
        assert os.environ[mod.ENV_INSTALLER_FILE] == "/old/Affinity.exe"
    finally:
        os.environ.pop(mod.ENV_INSTALLER_FILE, None)


def test_a_missing_installer_is_reported_not_raised_as_anything_else(monkeypatch):
    monkeypatch.setattr(aol, "_module", None)
    monkeypatch.setattr(aol.installer, "check_installer",
                        lambda script=None: (_ for _ in ()).throw(
                            aol.installer.InstallerNotFound("nowhere")))
    with pytest.raises(aol.NotAvailable):
        aol.module()
    monkeypatch.setattr(aol, "_module", None)
    assert aol.verify() and "nowhere" in aol.verify()[0]


# ── living inside AffinityOnLinux as AffinityManager/ ───────────────────────
#
# The merged layout is AffinityOnLinux/{AffinityScripts,AffinityManager}, so
# the installer is two directories up and across. These pin that, because the
# path is computed from __file__ and a directory rename would otherwise break
# it silently -- the manager would simply fall back to a fetched checkout and
# drive a different installer than the one it shipped with.

def test_the_vendored_installer_is_the_sibling_of_this_directory():
    from affinity_manager import installer

    root = Path(installer.__file__).resolve().parent.parent
    assert installer.vendored_installer() == \
        root.parent / "AffinityScripts" / installer.SCRIPT_NAME


def test_the_vendored_copy_outranks_a_fetched_checkout(monkeypatch):
    """A manager shipped inside AffinityOnLinux should drive the installer it
    shipped with, not one pulled from a branch."""
    from affinity_manager import installer, settings

    monkeypatch.delenv(installer.ENV_SCRIPT, raising=False)
    monkeypatch.setattr(settings, "get", lambda *a, **k: "")
    paths = installer.candidate_paths()
    assert paths.index(installer.vendored_installer()) < \
        paths.index(installer.managed_checkout() / "AffinityScripts"
                    / installer.SCRIPT_NAME)


def test_a_vendored_installer_is_not_warned_about_for_its_branch():
    """The branch warning is there because a checkout on main has an installer
    that ignores the prefix it is handed. Once merged, the branch is whatever
    AffinityOnLinux is on, and warning every time is how a real warning stops
    being read."""
    from affinity_manager import installer

    info = {
        "found": True, "script": "/x/AffinityScripts/AffinityLinuxInstaller.py",
        "branch": "main", "commit": "abc1234", "dirty": False,
        "supports_install_dir": True, "on_preferred_branch": False,
        "vendored": True,
    }
    line = installer.summary(info)
    assert installer.PREFERRED_BRANCH not in line
    assert "shipped with this manager" in line

    info["vendored"] = False
    assert installer.PREFERRED_BRANCH in installer.summary(info)
