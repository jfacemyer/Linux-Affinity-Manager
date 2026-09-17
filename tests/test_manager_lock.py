"""The window's half of the operation lock.

oplock itself is tested in test_oplock.py. What is tested here is the wiring:
that the manager's own operations actually go through the lock, that the
watchdog tick releases work which stopped without saying so, and that a
refusal or a hand release leaves the window in a coherent state.

The `window` fixture (conftest.py) is a ManagerWindow with the Qt removed --
real registry, real lock, real _busy_* methods.
"""
import inspect
import types

import pytest

from affinity_manager import oplock


@pytest.fixture
def two_prefixes(window, tmp_path):
    """A second managed prefix, so "somewhere else" is a real place."""
    w, reg, _ = window
    other = tmp_path / "Other"
    (other / "drive_c").mkdir(parents=True)
    (other / "dosdevices").mkdir(parents=True)
    reg.add("Other", str(other))
    return window


# -- the manager's own operations go through it -----------------------------

def test_starting_an_operation_claims_the_lock(window):
    w, _, _ = window
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")
    assert w.lock.held is not None and w.lock.held.label == "Clean"


def test_a_second_operation_is_refused(two_prefixes):
    w, _, _ = two_prefixes
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")
    with pytest.raises(oplock.InUse):
        w._busy_start("Cloning Other", 100, prefix="Other", operation="Clone")


def test_the_refused_operation_leaves_the_first_one_alone(two_prefixes):
    """A refusal must not half-start: no registry row, no log offset, and the
    prefix that was working is still the one recorded as working."""
    w, reg, _ = two_prefixes
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")
    with pytest.raises(oplock.InUse):
        w._busy_start("Cloning Other", 100, prefix="Other", operation="Clone")
    assert reg.by_name("Other").get("state") != "working"
    assert reg.by_name("Managed")["state"] == "working"
    assert w._busy_prefix == "Managed"


def test_finishing_releases_it(window):
    w, _, _ = window
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")
    w._busy_done("Done")
    assert w.lock.held is None
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")


def test_an_operation_with_no_prefix_claims_nothing(window):
    """Some indication is just indication -- a scan, a refresh -- and has no
    prefix to name. Those must not take the lock."""
    w, _, _ = window
    w._busy_start("Looking around")
    assert w.lock.held is None


# -- the watchdog -----------------------------------------------------------

def test_the_tick_releases_work_that_stopped_without_saying_so(window, monkeypatch):
    w, reg, prefixlog = window
    running = {"yes": True}
    w._busy_start("Cloning", 100, prefix="Managed", operation="Clone",
                  alive=lambda: running["yes"])
    past_grace = w.lock.held.started + oplock.OperationLock.SWEEP_GRACE + 1
    monkeypatch.setattr(oplock, "_now", lambda: past_grace)

    w._lock_tick()
    assert w.lock.held is not None      # still running, still held

    running["yes"] = False
    w._lock_tick()
    assert w.lock.held is None
    assert reg.by_name("Managed").get("state") != "working"
    assert "without reporting" in prefixlog.tail("Managed")


def test_the_tick_leaves_a_live_operation_alone(window, monkeypatch):
    w, reg, _ = window
    w._busy_start("Cloning", 100, prefix="Managed", operation="Clone",
                  alive=lambda: True)
    later = w.lock.held.started + 3600
    monkeypatch.setattr(oplock, "_now", lambda: later)
    w._lock_tick()
    assert w.lock.held is not None
    assert reg.by_name("Managed")["state"] == "working"


def test_the_tick_does_nothing_when_nothing_is_held(window):
    w, _, _ = window
    w._lock_tick()
    assert w.lock.held is None


# -- the caution row --------------------------------------------------------

def test_the_caution_row_follows_the_lock(window):
    w, _, _ = window
    shown = []
    w.lock_row.show = lambda: shown.append(True)
    w.lock_row.hide = lambda: shown.append(False)
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")
    assert shown[-1] is True
    w._busy_done("Done")
    assert shown[-1] is False


def test_the_banner_names_the_operation_and_its_prefix(window):
    w, _, _ = window
    said = []
    w.lock_banner.setText = said.append
    w._busy_start("Cleaning Managed", 3, prefix="Managed", operation="Clean")
    assert "Clean" in said[-1] and "Managed" in said[-1]


def test_an_unchanged_line_is_not_repainted(window):
    """The watchdog ticks forever; an idle manager should do no work in it."""
    w, _, _ = window
    said = []
    w.lock_row.show = lambda: said.append("show")
    w.lock_row.hide = lambda: said.append("hide")
    w._sync_lock_ui()
    w._sync_lock_ui()
    w._sync_lock_ui()
    assert said.count("hide") <= 1


# -- releasing by hand ------------------------------------------------------

def test_release_by_hand_frees_the_lock_and_says_so(window, monkeypatch):
    import AffinityLinuxManager as app
    w, reg, prefixlog = window
    monkeypatch.setattr(app.QMessageBox, "question",
                        staticmethod(lambda *a, **k: app.QMessageBox.StandardButton.Ok))
    w._busy_start("Cloning", 100, prefix="Managed", operation="Clone",
                  alive=lambda: True)
    types.MethodType(app.ManagerWindow._release_lock, w)()
    assert w.lock.held is None
    assert reg.by_name("Managed").get("state") != "working"
    assert "released by hand" in prefixlog.tail("Managed")


def test_declining_the_question_keeps_the_lock(window, monkeypatch):
    import AffinityLinuxManager as app
    w, _, _ = window
    monkeypatch.setattr(app.QMessageBox, "question",
                        staticmethod(lambda *a, **k: app.QMessageBox.StandardButton.Cancel))
    w._busy_start("Cloning", 100, prefix="Managed", operation="Clone",
                  alive=lambda: True)
    types.MethodType(app.ManagerWindow._release_lock, w)()
    assert w.lock.held is not None


# -- the list cannot hand its buttons back mid-operation --------------------

def test_a_refresh_during_an_operation_does_not_re_enable_the_list():
    """refresh() calls _selection_changed, so the guard has to live there too
    and not only in _sync_lock_ui."""
    import AffinityLinuxManager as app

    class Button:
        def __init__(self):
            self.enabled = True

        def setEnabled(self, value):
            self.enabled = value

    def must_not_be_reached():
        raise AssertionError("_selection_changed went past the lock guard")

    stub = types.SimpleNamespace(
        lock=oplock.OperationLock(),
        BUSY_BUTTONS=("clone_button", "delete_button"),
        clone_button=Button(),
        delete_button=Button(),
        selected_entry=must_not_be_reached,
    )
    stub.lock.claim("Managed", "Clone")
    types.MethodType(app.ManagerWindow._selection_changed, stub)()
    assert stub.clone_button.enabled is False
    assert stub.delete_button.enabled is False


# -- the footer belongs to the window, not to the list page -----------------

def test_the_status_row_sits_outside_the_stack():
    """Read off the source rather than built, because building the window
    probes the real home. The point is that Setup keeps the status line: a
    status row added to the list page would vanish on the way into it."""
    import AffinityLinuxManager as app
    source = inspect.getsource(app.ManagerWindow._build)
    assert "shell_layout.addWidget(status_row" in source
    assert "middle_layout.addWidget(status_row" not in source
    assert source.index("shell_layout.addWidget(self.stack") < \
           source.index("shell_layout.addWidget(status_row")
