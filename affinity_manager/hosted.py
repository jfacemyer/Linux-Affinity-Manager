"""The AffinityOnLinux installer, hosted as a page of the manager window.

Not launched as a child process any more. The manager is the main window and
Setup is a state of it, which means the installer's window has to stop being a
window: reparented into the manager's QStackedWidget it keeps its layout and
loses its title bar, its own size constraints and its independent lifetime.

Four things follow from that, and each is handled here rather than upstream,
because upstream is still a single file that people run with `curl | python3`
and it has to keep working that way.

  * Size. The constructor sets a minimum of half the screen, which is right for
    a window of its own and would make the manager unresizable. Cleared after
    construction, and the page scrolls, so a tall installer UI stays reachable
    in a short window.

  * Which prefix. AFFINITY_INSTALL_DIR is read once, in the constructor. As a
    child process that was the child's environment; here it is ours, so it is
    set for the length of the construction and put back afterwards -- and the
    directory the window settled on is checked against the one asked for before
    the page is shown at all. An installer pointed at the wrong prefix is the
    failure this whole project exists to prevent.

  * Time. Three deferred tasks are scheduled 50ms, 500ms and 700ms after the
    constructor returns: background probing, a DXVK/vkd3d check, and a donation
    dialog. Hosted, the first is fine, the second belongs to entering the page
    rather than to building it, and the third is a modal appearing over a
    manager the user did not ask a question of.

  * One operation at a time. The installer's own start_operation /
    end_operation are the choke point -- not every button click is an operation,
    and a theme toggle must not be refused -- so the lock is taken and given up
    there.
"""

from __future__ import annotations

import contextlib
import io
import threading
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import aol, oplock, prefixlog, prefixstate


class WrongTarget(RuntimeError):
    """The installer did not end up pointing at the prefix it was given.

    Shown rather than worked around. The installer remembers the last location
    it was told to use, and a version that ignored the environment would
    silently drive that one instead -- which, for somebody with a working
    prefix and a test prefix, is the worst thing this application could do."""


# ── thread exceptions ────────────────────────────────────────────────────────
#
# The installer does its work in daemon threads. On its own that is fine: a
# thread that dies prints a traceback to the terminal it was started from.
# Hosted there may be no terminal, and a dead thread is also a lock nobody will
# release, so the death has to reach the log that belongs to the prefix.

_previous_excepthook = None
_hook_owner = None          # the page whose prefix a thread exception belongs to


def _thread_excepthook(args):
    page = _hook_owner
    if page is not None and args.exc_type is not SystemExit:
        with contextlib.suppress(Exception):
            page.thread_died(args)
    if _previous_excepthook is not None:
        _previous_excepthook(args)


def _install_thread_excepthook():
    global _previous_excepthook
    if _previous_excepthook is None:
        _previous_excepthook = threading.excepthook
        threading.excepthook = _thread_excepthook


# ── the window, made hostable ────────────────────────────────────────────────

_hosted_cls = None


def hosted_class():
    """AffinityInstallerGUI with the window-ness taken out.

    Built on first use rather than at import, because the base class does not
    exist until the installer has been located and loaded, and a manager whose
    import fails because no installer is on disk would be useless for the one
    thing that could fix it."""
    global _hosted_cls
    if _hosted_cls is not None:
        return _hosted_cls

    base = aol.cls()

    class HostedInstaller(base):
        """One installer window, living inside the manager."""

        def __init__(self, page):
            super().__init__()
            # Set after super(), which is the only order sip allows, so the
            # deferred tasks scheduled in there read these through getattr.
            self._page = page
            self._donation_suppressed = True
            # The constructor's minimum is half the screen. Right for a window,
            # wrong for a page: it would set the floor for the whole manager.
            self.setMinimumSize(0, 0)

        # -- deferred tasks that belong to entering, not to building ---------

        def _check_and_update_dxvk_vkd3d(self):
            """Skip the constructor's 500ms call; the page runs this on entry.

            Building the page and entering it are the same moment the first
            time and different moments every time after, and this check should
            happen on every entry -- a prefix can gain a Wine installation
            while the manager is looking at something else."""
            if not getattr(self, "_entered", False):
                return
            super()._check_and_update_dxvk_vkd3d()

        def show_donation_dialog(self):
            """Suppress the automatic one; keep the button.

            The 700ms timer would put a modal over a manager window the user
            was in the middle of using, on every entry into Setup. The Donate
            button calls this same method and is left working, which is the
            part that was ever the user's own choice."""
            if getattr(self, "_donation_suppressed", False):
                self._donation_suppressed = False
                return
            super().show_donation_dialog()

        # -- the lock -------------------------------------------------------

        def start_operation(self, operation_name):
            page = getattr(self, "_page", None)
            if page is not None:
                try:
                    page.operation_started(operation_name)
                except oplock.InUse as exc:
                    # Said twice on purpose: show_message is the dialog the
                    # user sees, and the raise is what stops the caller from
                    # starting the thread it was about to start.
                    self.show_message("Something else is running", str(exc),
                                      "warning")
                    raise
            super().start_operation(operation_name)

        def end_operation(self):
            super().end_operation()
            page = getattr(self, "_page", None)
            if page is not None:
                page.operation_finished()

        # -- the record -----------------------------------------------------

        def log(self, message, level="info"):
            """Mirror into the prefix's own log as well as the installer's.

            ~/AffinitySetup.log is shared by every run and is where upstream
            puts this; the per-prefix log is what the manager reads back when
            it has to say what an interrupted operation was doing."""
            super().log(message, level)
            page = getattr(self, "_page", None)
            if page is not None and str(message).strip():
                with contextlib.suppress(Exception):
                    prefixlog.write(page.name, str(message))

        def _save_install_location(self, directory):
            """Not persisted from here.

            This writes the installer's remembered location, which a later
            standalone run would open on. The manager already knows which
            prefix is which, and a look at a scratch prefix inside the manager
            should not move where the installer opens by itself afterwards."""
            self.log("Install location not persisted: the manager holds it.",
                     "info")

    _hosted_cls = HostedInstaller
    return _hosted_cls


# ── the page ─────────────────────────────────────────────────────────────────

class SetupPage(QWidget):
    """Setup for one prefix: a header, and the installer under it."""

    def __init__(self, manager, entry):
        super().__init__()
        self.manager = manager
        self.entry = entry
        self.name = entry["name"]
        self.path = Path(entry["path"])
        self._last_progress_text = None

        _install_thread_excepthook()

        # Built with the environment pointed at this prefix, and only this
        # prefix, for exactly as long as the constructor runs.
        with aol.target_env(self.path, entry.get("installer_file")):
            self.installer = hosted_class()(self)

        settled = Path(self.installer.directory).expanduser()
        if settled.resolve() != self.path.resolve():
            self.installer.close()
            self.installer.deleteLater()
            raise WrongTarget(
                "The installer was given %s and settled on %s. It has not been "
                "shown, because driving the wrong prefix is worse than not "
                "being able to drive one." % (self.path, settled))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 20, 12)
        header_layout.setSpacing(12)
        self.back_button = QPushButton("← Prefixes")
        self.back_button.setToolTip("Back to the prefix list")
        self.back_button.clicked.connect(manager.show_list)
        header_layout.addWidget(self.back_button, 0)
        title = QLabel("Setup — %s" % self.name)
        title.setObjectName("sectionTitle")
        header_layout.addWidget(title, 0)
        where = QLabel(str(self.path))
        where.setObjectName("descriptionLabel")
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(where, 1)
        layout.addWidget(header, 0)

        # Scrolled, because the installer lays out for a window of its own and
        # the manager can be shorter than that. widgetResizable so it uses the
        # full width when there is width to use.
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(self.installer)      # reparents: no longer a window
        layout.addWidget(area, 1)

    # -- entering and leaving -------------------------------------------------

    def entered(self):
        """Called every time this page becomes the visible one."""
        self.installer._entered = True
        self._say_where_we_are()
        self._clear_stalled_wine()
        self.installer._check_and_update_dxvk_vkd3d()

    def _say_where_we_are(self):
        """The shared status line still holds whatever the list left on it.

        One line, two states: the same bar that said "nothing managed yet" a
        moment ago is now the Setup page's, and leaving the list's sentence
        there would describe the wrong thing entirely."""
        if self.manager.lock.held is not None:
            return                      # an operation's own reporting outranks this
        condition = prefixstate.classify(self.path, entry=self.entry)
        self.manager.status.setText("%s — %s" % (self.name, condition.detail))

    def _clear_stalled_wine(self):
        """Clear leftover Wine helpers in THIS prefix, and no other.

        Upstream runs this from main(), which the manager never calls. It scopes
        itself by /proc/<pid>/environ and refuses entirely if Affinity is live
        in the target, so the worst it can do here is nothing. Its report goes
        to stdout, which hosted is nobody's terminal, so it is captured into the
        log instead."""
        module = aol.module()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                module.kill_stalled_wine_processes(prefix=str(self.path))
        except Exception as exc:
            self.installer.log("Could not check for stalled Wine processes: %s"
                               % exc, "warning")
            return
        for line in out.getvalue().splitlines():
            if line.strip():
                self.installer.log(line.strip(), "info")

    # -- the lock, and the manager's status line ------------------------------

    def operation_started(self, label):
        """Claim, record and report. Raises oplock.InUse if refused."""
        self._last_progress_text = None
        self.manager._busy_start(
            "%s — %s" % (self.name, label), 100,
            prefix=self.name, operation=label,
            # The installer's own flag is the oracle. It is set by the
            # start_operation this call precedes, so for a moment it still
            # reads False -- which is what OperationLock.SWEEP_GRACE covers.
            alive=lambda: bool(getattr(self.installer, "operation_in_progress",
                                       False)))
        self.installer.progress_text_signal.connect(self._progress_text)
        self.installer.progress_signal.connect(self._progress)

    def operation_finished(self):
        for signal, slot in ((self.installer.progress_text_signal, self._progress_text),
                             (self.installer.progress_signal, self._progress)):
            with contextlib.suppress(TypeError):
                signal.disconnect(slot)
        self.manager._busy_done("%s — finished" % self.name)

    def _progress_text(self, text):
        """The installer's progress line, shown on the manager's status bar.

        Deliberately not manager._busy_step: that calls processEvents, and this
        arrives on a queued signal from a worker thread, so processing events
        from inside it would be re-entering the event loop from a slot."""
        if not text or text == self._last_progress_text:
            return
        self._last_progress_text = text
        self.manager.status.setText("%s — %s" % (self.name, text))

    def _progress(self, fraction):
        bar = self.manager.progress
        top = bar.maximum()
        if top:
            bar.setValue(max(0, min(top, int(fraction * top))))

    # -- availability ---------------------------------------------------------

    def set_actions_enabled(self, enabled):
        """Grey the installer's own buttons while another prefix is working.

        Asymmetric on purpose. Disabling is done here, one button at a time,
        because the lock is the only thing that knows about it. Re-enabling is
        NOT: the installer disables buttons for its own reasons -- there is no
        Wine in the prefix yet, nothing is installed to update -- and switching
        them all back on would offer actions it had already ruled out. So the
        way back is to ask the installer to work out its own button states
        again."""
        buttons = getattr(self.installer, "_all_action_buttons", None) or []
        if not enabled:
            for button in buttons:
                with contextlib.suppress(Exception):
                    button.setEnabled(False)
            self._actions_disabled = True
            return
        if getattr(self, "_actions_disabled", False):
            self._actions_disabled = False
            with contextlib.suppress(Exception):
                self.installer.refresh_status_signal.emit()

    # -- a thread that died ---------------------------------------------------

    def thread_died(self, args):
        """A worker thread raised and nobody caught it.

        Logged rather than absorbed: the lock's watchdog will notice within a
        few seconds that the operation has stopped, but it cannot say why, and
        this is the only place that can."""
        prefixlog.write(self.name, "A background task ended with %s: %s"
                        % (args.exc_type.__name__, args.exc_value))
        with contextlib.suppress(Exception):
            self.installer.log("A background task ended with %s: %s"
                               % (args.exc_type.__name__, args.exc_value),
                               "error")

    # -- teardown -------------------------------------------------------------

    def dispose(self):
        """Close the installer this page holds.

        Only ever called for a page whose prefix is not doing anything: the
        installer's threads are daemon threads with no join, so tearing one down
        mid-operation would leave them writing into a window that has gone."""
        global _hook_owner
        if _hook_owner is self:
            _hook_owner = None
        with contextlib.suppress(Exception):
            self.installer.close()
        self.installer.deleteLater()


def own_thread_exceptions(page):
    """Point the thread excepthook at the page currently being worked on."""
    global _hook_owner
    _hook_owner = page
