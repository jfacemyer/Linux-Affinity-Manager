"""Hosting the installer inside the manager window.

The real AffinityInstallerGUI is 19,000 lines and downloads things when it
starts, so what is tested here is the hosting -- the overrides, the guard on
which prefix got targeted, and the lock -- against a stand-in with the same
shape. The one-process run against a real installer is done by hand; this is
what stops it regressing silently afterwards.

The guard worth the most is test_a_window_that_settled_elsewhere_is_refused.
Driving the wrong prefix is the failure this whole application exists to
prevent.
"""
import os
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton  # noqa: E402

from affinity_manager import aol, hosted, oplock, prefixlog, registry  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class FakeInstaller(QMainWindow):
    """As much of AffinityInstallerGUI as the hosting touches."""

    progress_signal = pyqtSignal(float)
    progress_text_signal = pyqtSignal(str)
    refresh_status_signal = pyqtSignal()

    settle_at = None                 # set by a test to fake ignoring the env

    def __init__(self):
        super().__init__()
        self.setMinimumSize(900, 700)          # the real one does this too
        self.directory = self.settle_at or os.environ.get("AFFINITY_INSTALL_DIR", "")
        self.operation_in_progress = False
        self.messages = []
        self.logged = []
        self.donations = 0
        self.dxvk_checks = 0
        self.operations = []
        self.refreshes = 0
        self._all_action_buttons = [QPushButton("One"), QPushButton("Two")]
        self.refresh_status_signal.connect(self._count_refresh)

    def _count_refresh(self):
        self.refreshes += 1

    def log(self, message, level="info"):
        self.logged.append((message, level))

    def show_message(self, title, message, kind="info"):
        self.messages.append((title, message, kind))

    def show_donation_dialog(self):
        self.donations += 1

    def _check_and_update_dxvk_vkd3d(self):
        self.dxvk_checks += 1

    def start_operation(self, name):
        self.operations.append(name)
        self.operation_in_progress = True

    def end_operation(self):
        self.operation_in_progress = False

    def _save_install_location(self, directory):
        raise AssertionError("the hosted installer must not persist this")


@pytest.fixture
def hosting(qapp, tmp_path, monkeypatch):
    """A manager stand-in, a fake installer, and a prefix to point at."""
    monkeypatch.setattr(registry, "manager_dir", lambda: tmp_path)
    monkeypatch.setattr(hosted, "_hosted_cls", None)
    monkeypatch.setattr(hosted, "_hook_owner", None)
    FakeInstaller.settle_at = None

    killed = []
    fake_module = types.SimpleNamespace(
        ENV_INSTALL_DIR="AFFINITY_INSTALL_DIR",
        ENV_INSTALLER_FILE="AFFINITY_INSTALLER_FILE",
        kill_stalled_wine_processes=lambda prefix=None: killed.append(prefix),
    )
    monkeypatch.setattr(aol, "module", lambda script=None: fake_module)
    monkeypatch.setattr(aol, "cls", lambda: FakeInstaller)
    monkeypatch.delenv("AFFINITY_INSTALL_DIR", raising=False)

    prefix = tmp_path / "Scratch"
    (prefix / "drive_c").mkdir(parents=True)
    (prefix / "dosdevices").mkdir(parents=True)

    manager = types.SimpleNamespace(
        lock=oplock.OperationLock(),
        status=types.SimpleNamespace(setText=lambda *_: None),
        progress=types.SimpleNamespace(maximum=lambda: 100, setValue=lambda *_: None),
        show_list=lambda: None,
        _busy_start=None, _busy_done=None,
    )
    started, finished = [], []

    def busy_start(message, maximum=0, *, prefix=None, operation=None, alive=None):
        manager.lock.claim(prefix, operation or message, alive=alive)
        started.append((prefix, operation))

    def busy_done(message=""):
        manager.lock.release()
        finished.append(message)

    manager._busy_start = busy_start
    manager._busy_done = busy_done

    entry = {"name": "Scratch", "path": str(prefix)}
    return types.SimpleNamespace(manager=manager, entry=entry, prefix=prefix,
                                 killed=killed, started=started,
                                 finished=finished)


def page_for(h):
    return hosted.SetupPage(h.manager, h.entry)


# -- which prefix -----------------------------------------------------------

def test_the_page_points_the_installer_at_its_own_prefix(hosting):
    page = page_for(hosting)
    assert Path(page.installer.directory) == hosting.prefix


def test_a_window_that_settled_elsewhere_is_refused(hosting, tmp_path):
    """The installer remembers the last location it was told to use. A copy
    that ignored the environment would drive that one instead, and for somebody
    with a working prefix and a test prefix that is the worst thing this
    application could do."""
    FakeInstaller.settle_at = str(tmp_path / "SomewhereElse")
    with pytest.raises(hosted.WrongTarget) as caught:
        page_for(hosting)
    assert "SomewhereElse" in str(caught.value)
    assert str(hosting.prefix) in str(caught.value)


def test_the_environment_is_put_back(hosting):
    """Left set, it would leak the last-entered prefix into every subprocess
    the manager spawned afterwards."""
    os.environ["AFFINITY_INSTALL_DIR"] = "/somewhere/of/its/own"
    try:
        page_for(hosting)
        assert os.environ["AFFINITY_INSTALL_DIR"] == "/somewhere/of/its/own"
    finally:
        os.environ.pop("AFFINITY_INSTALL_DIR", None)


def test_the_environment_goes_away_again_when_it_was_not_set(hosting):
    page_for(hosting)
    assert "AFFINITY_INSTALL_DIR" not in os.environ


# -- window-ness removed ----------------------------------------------------

def test_the_installer_stops_being_a_window(hosting):
    page = page_for(hosting)
    assert page.installer.isWindow() is False
    assert page.installer.minimumSize().width() == 0
    assert page.installer.minimumSize().height() == 0


# -- deferred tasks ---------------------------------------------------------

def test_the_dxvk_check_waits_for_the_page_to_be_entered(hosting):
    page = page_for(hosting)
    page.installer._check_and_update_dxvk_vkd3d()
    assert page.installer.dxvk_checks == 0
    page.entered()
    assert page.installer.dxvk_checks == 1


def test_the_automatic_donation_dialog_is_suppressed_once(hosting):
    """The 700ms timer's call is dropped; the Donate button still works."""
    page = page_for(hosting)
    page.installer.show_donation_dialog()          # the timer
    assert page.installer.donations == 0
    page.installer.show_donation_dialog()          # the button
    assert page.installer.donations == 1


def test_entering_clears_stalled_wine_in_this_prefix_only(hosting):
    page = page_for(hosting)
    page.entered()
    assert hosting.killed == [str(hosting.prefix)]


def test_the_install_location_is_not_persisted(hosting):
    """FakeInstaller raises if the base implementation is reached."""
    page = page_for(hosting)
    page.installer._save_install_location("/anywhere")


# -- the lock ---------------------------------------------------------------

def test_an_operation_claims_the_lock(hosting):
    page = page_for(hosting)
    page.installer.start_operation("Setup Wine Environment")
    assert hosting.manager.lock.held.prefix == "Scratch"
    assert page.installer.operations == ["Setup Wine Environment"]


def test_an_operation_is_refused_while_another_prefix_works(hosting):
    page = page_for(hosting)
    hosting.manager.lock.claim("Other", "Install Affinity")
    with pytest.raises(oplock.InUse):
        page.installer.start_operation("Setup Wine Environment")
    # Refused, and the installer never started it.
    assert page.installer.operations == []
    assert page.installer.messages and "running" in page.installer.messages[0][0]


def test_finishing_an_operation_releases_the_lock(hosting):
    page = page_for(hosting)
    page.installer.start_operation("Setup Wine Environment")
    page.installer.end_operation()
    assert hosting.manager.lock.held is None
    assert page.installer.operation_in_progress is False


def test_the_watchdog_oracle_follows_the_installers_own_flag(hosting):
    page = page_for(hosting)
    page.installer.start_operation("Setup Wine Environment")
    operation = hosting.manager.lock.held
    assert operation.finished() is False
    page.installer.operation_in_progress = False
    assert operation.finished() is True


# -- availability -----------------------------------------------------------

def test_another_prefix_working_greys_this_pages_buttons(hosting):
    page = page_for(hosting)
    page.set_actions_enabled(False)
    assert all(not b.isEnabled() for b in page.installer._all_action_buttons)


def test_re_enabling_asks_the_installer_rather_than_switching_them_all_on(hosting, qapp):
    """The installer disables buttons for its own reasons -- no Wine yet,
    nothing installed to update -- so turning them all back on would offer
    actions it had already ruled out."""
    page = page_for(hosting)
    page.installer._all_action_buttons[0].setEnabled(False)   # its own decision
    page.set_actions_enabled(False)
    page.set_actions_enabled(True)
    qapp.processEvents()
    assert page.installer.refreshes == 1
    assert not page.installer._all_action_buttons[0].isEnabled()


def test_re_enabling_does_nothing_when_nothing_was_disabled(hosting, qapp):
    page = page_for(hosting)
    page.set_actions_enabled(True)
    qapp.processEvents()
    assert page.installer.refreshes == 0


# -- the record -------------------------------------------------------------

def test_log_lines_reach_the_prefixs_own_log(hosting):
    page = page_for(hosting)
    page.installer.log("Setting up Wine", "info")
    assert "Setting up Wine" in prefixlog.tail("Scratch")


def test_a_thread_that_died_is_reported_and_the_old_hook_still_runs(hosting):
    page = page_for(hosting)
    hosted.own_thread_exceptions(page)
    seen = []
    try:
        previous = hosted._previous_excepthook
        hosted._previous_excepthook = seen.append
        args = threading.ExceptHookArgs(
            (RuntimeError, RuntimeError("wine exploded"), None, None))
        hosted._thread_excepthook(args)
    finally:
        hosted._previous_excepthook = previous
    assert "wine exploded" in prefixlog.tail("Scratch")
    assert seen == [args]


def test_a_thread_exception_after_disposal_reaches_nobody(hosting):
    page = page_for(hosting)
    hosted.own_thread_exceptions(page)
    page.dispose()
    args = threading.ExceptHookArgs(
        (RuntimeError, RuntimeError("too late"), None, None))
    try:
        previous = hosted._previous_excepthook
        hosted._previous_excepthook = lambda _a: None
        hosted._thread_excepthook(args)          # must not raise
    finally:
        hosted._previous_excepthook = previous
    assert "too late" not in prefixlog.tail("Scratch")
