#!/usr/bin/env python3
"""Affinity on Linux Manager -- several Affinity prefixes, tracked.

AffinityOnLinux installs one Affinity, into ~/.AffinityLinux, and remembers one
custom location. That is the right shape for most people and the wrong shape as
soon as you need two: a working install you cannot afford to break, and a new
release to try.

This manages the set. It does not install anything itself -- it runs the
AffinityOnLinux installer against a prefix you name, which is the only part
that was missing.

    ./AffinityLinuxManager.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from affinity_manager import (
    __version__,
    aol,
    coldstart,
    commands as commands_mod,
    desktopentry,
    discover,
    hosted,
    installer,
    prefsseed,
    removal,
    snapshots,
    oplock,
    prefixlog,
    prefixstate,
    probe,
    singleinstance,
    registry,
    ui,
    maintenance,
)


class ProbeThread(QThread):
    """Prefix inspection off the UI thread.

    disk_usage walks the tree, and a prefix is several gigabytes across tens of
    thousands of files on a disk that may be spun down. Doing that inline froze
    the window for seconds at a time."""

    done = pyqtSignal(list)

    def __init__(self, entries):
        super().__init__()
        self.entries = list(entries)

    def run(self):
        """One unreadable prefix must not cost the others, or the window.

        probe.describe walks the prefix, and iterdir raises for a directory
        that can be traversed but not listed: a prefix installed under sudo, a
        mode-0111 directory, an sshfs or NFS mount whose connection has
        dropped so is_dir() answers from cache and iterdir() returns ESTALE.
        Unguarded, that exception left QThread.run and Qt aborted the process
        -- the manager vanished, with no message, because one row in a list
        could not be measured."""
        rows = []
        for entry in self.entries:
            try:
                info = probe.describe(entry["path"])
                info["size"] = (probe.disk_usage(entry["path"])
                                if info["exists"] else 0)
            except Exception as exc:
                info = {"path": entry["path"], "exists": False,
                        "is_prefix": False, "has_affinity": False,
                        "affinity_version": None, "wine": None,
                        "wine_builds": [], "running": False, "pids": [],
                        "size": 0, "unreadable": str(exc)}
            rows.append((entry, info))
        self.done.emit(rows)


class CloneThread(QThread):
    """Copying a prefix off the UI thread.

    Several gigabytes across tens of thousands of files. Inline, the window
    stops answering the compositor and the desktop offers to kill it."""

    done = pyqtSignal(list, str)
    progress = pyqtSignal(int)

    def __init__(self, plan):
        super().__init__()
        self.plan = plan

    def run(self):
        try:
            problems = maintenance.clone(self.plan, progress=self.progress.emit)
        except Exception as exc:                      # surfaced, not swallowed
            self.done.emit([], str(exc))
            return
        self.done.emit(problems, "")


def fit_to_contents(dialog, *, min_width=420):
    """Size a dialog to what it holds, once, on first show.

    Every dialog here used to carry a hand-picked setMinimumWidth, which is a
    guess that is wrong as soon as the content changes -- a prefix with five
    Wine builds and long names needs a wider box than one with two, and the
    fixed width either clipped the text or left half the dialog empty.

    Qt will do it: adjustSize() asks the layout what it wants. The parts worth
    adding are a floor, so a nearly empty dialog is not a postage stamp, and a
    ceiling at the available screen, so a list of thirty items does not open
    taller than the display and put its buttons out of reach."""
    dialog.adjustSize()
    want = dialog.sizeHint()
    screen = dialog.screen() or QApplication.primaryScreen()
    avail = screen.availableGeometry() if screen else None

    width = max(want.width(), min_width)
    height = want.height()
    if avail:
        width = min(width, int(avail.width() * 0.9))
        height = min(height, int(avail.height() * 0.85))
    dialog.resize(width, height)


class SizedDialog(QDialog):
    """A dialog that sizes itself to its contents the first time it is shown.

    Done in showEvent rather than at the end of __init__ because a layout does
    not know what it wants until its widgets have been polished by the
    stylesheet -- font sizes and padding come from ui.apply(), and asking
    before that gives a box sized for the wrong font."""

    FIT_MIN_WIDTH = 420

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            fit_to_contents(self, min_width=self.FIT_MIN_WIDTH)


class ConfirmDialog(SizedDialog):
    """A destructive action, behind a deliberate tick and a button.

    This replaces typing the prefix name to confirm. Retyping a name proves
    dexterity, not intent -- and the name was printed on the same screen, so it
    was a copying exercise. A tick that arms the button is two deliberate acts
    against the same question, which is the thing actually worth requiring, and
    it does not punish anyone whose prefix is called
    AffinityLinux_26-09-16_-_Working_3.3_Test."""

    def __init__(self, parent, title, message, *, tick="Yes, I really want to do this",
                 confirm="Delete"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        ui.apply(self)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)

        self.tick = QCheckBox(tick)
        layout.addWidget(self.tick)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(confirm)
        self.ok.setEnabled(False)
        self.ok.setProperty("class", "danger")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # The tick arms the button; unticking disarms it again.
        self.tick.toggled.connect(self.ok.setEnabled)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        fit_to_contents(self, min_width=520)

    @staticmethod
    def ask(parent, title, message, **kwargs) -> bool:
        dialog = ConfirmDialog(parent, title, message, **kwargs)
        return dialog.exec() == QDialog.DialogCode.Accepted and dialog.tick.isChecked()


class BuildChoiceDialog(SizedDialog):
    FIT_MIN_WIDTH = 720

    """Which Wine builds to carry into the clone.

    This used to be a yes/no -- "copy only the one it uses?" -- and it was
    wrong twice over. A stopped prefix cannot say which Wine it uses; the
    question was answered from the ElementalWarriorWine symlink, which has been
    observed naming 11.12 while the prefix ran 11.16, so the clone kept the
    wrong Wine. And the user could not see which one the answer meant.

    So: every build, its size, and why it might matter, ticked or not according
    to the same evidence but visible and overridable."""

    def __init__(self, parent, name, builds):
        super().__init__(parent)
        self.setWindowTitle(f"Wine builds to copy from {name}")
        self.setModal(True)
        ui.apply(self)
        self.builds = builds
        self.boxes = []

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Which Wine builds should the copy get? A prefix only needs the one "
            "it runs, and each is roughly 800 MB.\n\n"
            "If nothing is running, the only evidence is the ElementalWarriorWine "
            "symlink, and that symlink can be stale -- so check this against what "
            "you know you launch."
        )
        blurb.setWordWrap(True)
        blurb.setObjectName("descriptionLabel")
        layout.addWidget(blurb)

        listing = QWidget()
        inner = QVBoxLayout(listing)
        inner.setContentsMargins(0, 0, 0, 0)
        for b in builds:
            box = QCheckBox(f"{b['name']}   {probe.human_size(b['size'])}   --  {b['note']}")
            box.setChecked(b["suggested"])
            inner.addWidget(box)
            self.boxes.append(box)
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(listing)
        scroll.setMinimumHeight(180)
        layout.addWidget(scroll)

        row = QHBoxLayout()
        all_button = QPushButton("Select all")
        none_button = QPushButton("Select none")
        all_button.clicked.connect(lambda: [b.setChecked(True) for b in self.boxes])
        none_button.clicked.connect(lambda: [b.setChecked(False) for b in self.boxes])
        row.addWidget(all_button)
        row.addWidget(none_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.total = QLabel()
        self.total.setObjectName("descriptionLabel")
        layout.addWidget(self.total)
        for box in self.boxes:
            box.toggled.connect(self._retotal)
        self._retotal()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Copy with these")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _retotal(self):
        keep = sum(b["size"] for b, box in zip(self.builds, self.boxes) if box.isChecked())
        n = sum(1 for box in self.boxes if box.isChecked())
        warn = "" if n else "   -- a prefix with no Wine cannot be launched from here"
        self.total.setText(
            f"{n} of {len(self.builds)} builds, {probe.human_size(keep)} of Wine{warn}")

    def chosen(self):
        return [b["name"] for b, box in zip(self.builds, self.boxes) if box.isChecked()]


class FirstRunDialog(SizedDialog):
    """The first time this is opened, when something is already installed.

    The whole dialog exists to not do anything by itself. Somebody has a prefix
    they have been using for a year; the manager has just noticed it. Taking it
    over, or moving it, without being told to would be exactly the behaviour
    this application is meant to be the opposite of.

    So the two things it can do are offered separately, each with the
    explanation built by coldstart from the same facts the action uses, and
    neither is preselected."""

    FIT_MIN_WIDTH = 640

    IN_PLACE = "in-place"
    MOVE = "move"
    SKIP = "skip"

    def __init__(self, parent, situation):
        super().__init__(parent)
        self.setWindowTitle("Affinity is already installed here")
        self.setModal(True)
        ui.apply(self)
        self.situation = situation
        self.install = situation.installs[0]
        self.choice = self.SKIP
        self._sizes = {}

        layout = QVBoxLayout(self)
        several = len(situation.installs) > 1
        blurb = QLabel(
            ("These are Affinity installs this manager did not create."
             if several else
             f"<b>{Path(self.install['path'])}</b> is an Affinity install this "
             "manager did not create.")
            + "<br><br>Nothing has been changed. Choose what should happen"
              + (" to the one you pick" if several else " to it")
              + "; you can also do nothing now and decide later."
              + ("<br>The rest stay where they are and are offered again under "
                 "<i>Find installations</i>." if several else ""))
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        form = QFormLayout()
        # One dialog, one decision -- but say so by letting the user choose
        # WHICH, rather than silently acting on whichever was modified most
        # recently. Saying "3 installations found" and then only ever being
        # able to act on installs[0] was the previous behaviour.
        self.picker = None
        if several:
            self.picker = QComboBox()
            for item in situation.installs:
                self.picker.addItem(item["path"], item)
            self.picker.currentIndexChanged.connect(self._install_changed)
            form.addRow("Which one", self.picker)

        self.name_edit = QLineEdit(
            self.install.get("suggested_name") or "Affinity")
        form.addRow("Call it", self.name_edit)
        layout.addLayout(form)

        self.in_place_text = self._section("Manage it where it is")
        self.move_text = self._section(f"Move it into {situation.base}")
        # Debounced. _retell measures the prefix, and connecting it straight to
        # textChanged ran a several-gigabyte du on every keystroke.
        self._retell_timer = QTimer(self)
        self._retell_timer.setSingleShot(True)
        self._retell_timer.setInterval(350)
        self._retell_timer.timeout.connect(self._retell)
        self.name_edit.textChanged.connect(self._retell_timer.start)
        self._retell()

        row = QHBoxLayout()
        later = QPushButton("Decide later")
        later.clicked.connect(self.reject)
        row.addWidget(later)
        row.addStretch(1)
        in_place = QPushButton("Manage in place")
        in_place.clicked.connect(lambda: self._choose(self.IN_PLACE))
        row.addWidget(in_place)
        move = QPushButton("Move, then manage")
        move.clicked.connect(lambda: self._choose(self.MOVE))
        row.addWidget(move)
        layout.addLayout(row)

    def _section(self, title):
        self.layout().addSpacing(10)
        heading = QLabel(f"<b>{title}</b>")
        self.layout().addWidget(heading)
        body = QLabel("")
        body.setWordWrap(True)
        body.setObjectName("descriptionLabel")
        self.layout().addWidget(body)
        return body

    def _install_changed(self):
        chosen = self.picker.currentData()
        if chosen is None:
            return
        self.install = chosen
        self.name_edit.setText(chosen.get("suggested_name") or "Affinity")
        self._retell()

    def _size_of(self, install):
        """Measured once per install, not once per keystroke."""
        path = install["path"]
        if path not in self._sizes:
            self._sizes[path] = probe.disk_usage(Path(path))
        return self._sizes[path]

    def _retell(self):
        name = self.name_edit.text().strip() or "Affinity"
        self.in_place_text.setText(
            " ".join(coldstart.explain_in_place(self.install)))
        self.move_text.setText(
            " ".join(coldstart.explain_move(self.install, name,
                                            base=self.situation.base,
                                            size=self._size_of(self.install))))

    def _choose(self, choice):
        try:
            registry.validate_name(self.name_edit.text().strip())
        except registry.InvalidName as exc:
            QMessageBox.warning(self, "That name will not do", str(exc))
            return
        self.choice = choice
        self.accept()

    def chosen_name(self):
        return self.name_edit.text().strip()


class CarrySettingsDialog(SizedDialog):
    """Bring preferences, recent files and drive letters across from another prefix.

    Run after Setup, before Affinity's first launch in the destination. After,
    because drive letters cannot exist until Wine has initialised the prefix --
    see prefsseed.is_initialised. Before first launch, because that is when the
    destination still holds only stock settings.

    Three things are shown, because each has been got wrong before:
      - what is copied, and what is deliberately left behind by name -- the
        source folder holds 200 MB of crash-recovery autosaves and a WebView2
        profile that are not settings;
      - the drive letters, each with how many recent files depend on it, since
        a recent file on W: is a dead link in a prefix without a W:;
      - whether the source is running, because Affinity rewrites
        preferences.dat and RecentFiles.xml as it goes."""

    FIT_MIN_WIDTH = 700

    def __init__(self, parent, dest_prefix, destination, sources):
        super().__init__(parent)
        self.setWindowTitle("Copy settings from another prefix")
        self.setModal(True)
        ui.apply(self)
        self.dest_prefix = Path(dest_prefix)
        self.destination = Path(destination)
        self.sources = list(sources)
        self.manual = None
        self.letter_boxes = []

        layout = QVBoxLayout(self)
        populated = bool(prefsseed.settings_in(self.dest_prefix))
        opening = ("This prefix already has Affinity settings in it; they will "
                   "be renamed aside, not deleted."
                   if populated else
                   "This prefix has no Affinity settings yet.")
        blurb = QLabel(
            opening + (
                " Your preferences, workspaces, shortcuts, recent files and "
                "drive letters can be brought across from another prefix."
                if self.sources else
                " No other managed prefix has any to copy, but a backup or a "
                "prefix on another disk works just as well."))
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self.picker = QComboBox()
        for found in self.sources:
            self.picker.addItem(
                "%s — %s, %d files, %s" % (
                    found.prefix.name, found.version, found.files,
                    time.strftime("%Y-%m-%d", time.localtime(found.modified))),
                found)
        self.picker.addItem("Somewhere else…", None)
        self.picker.currentIndexChanged.connect(self._picked)
        form = QFormLayout()
        form.addRow("Copy from", self.picker)
        layout.addLayout(form)

        self.chosen_label = QLabel("")
        self.chosen_label.setWordWrap(True)
        self.chosen_label.setObjectName("descriptionLabel")
        layout.addWidget(self.chosen_label)

        self.running_label = QLabel("")
        self.running_label.setWordWrap(True)
        self.running_label.setObjectName("cautionText")
        layout.addWidget(self.running_label)

        layout.addSpacing(6)
        layout.addWidget(QLabel("<b>Drive letters</b>"))
        self.letters_note = QLabel("")
        self.letters_note.setWordWrap(True)
        self.letters_note.setObjectName("descriptionLabel")
        layout.addWidget(self.letters_note)
        self.letters_holder = QWidget()
        self.letters_layout = QVBoxLayout(self.letters_holder)
        self.letters_layout.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(self.letters_holder)

        self.replace = QCheckBox(
            "Replace what is in this prefix (what is there is renamed aside "
            "first, not deleted)")
        self.replace.setChecked(True)
        layout.addWidget(self.replace)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Copy")
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)
        self._picked()

    # -- the source ----------------------------------------------------------

    def _picked(self):
        source = self.picker.currentData()
        if source is None:
            self.chosen_label.setText(
                "Choose a prefix, or the Settings folder inside one. A backup "
                "or a copy on another disk works just as well.")
            self.running_label.setText("")
            self._letters_for(None)
            return
        self._describe(source)

    def _describe(self, source):
        plan = prefsseed.plan(source, self.destination)
        left = []
        for entry, kind in source.left_behind:
            if kind == prefsseed.VOLATILE:
                files, size, _ = prefsseed._measure(entry) if entry.is_dir() \
                    else (1, entry.stat().st_size, 0)
                left.append("%s (%s)" % (entry.name, probe.human_size(size))
                            if size > 1024 * 1024 else entry.name)
            else:
                left.append(entry.name + "?")
        self.chosen_label.setText(
            "%s\n%d setting file(s): %d new, %d replacing what is there.\n"
            "Also copied: %s.\nNot copied: %s.%s"
            % (source.path, len(plan.added) + len(plan.replaced),
               len(plan.added), len(plan.replaced),
               ", ".join(plan.extras) or "nothing else",
               ", ".join(left) or "nothing",
               "\n(Names ending in ? are not recognised as settings, so they "
               "are left where they are rather than guessed at.)"
               if any(n.endswith("?") for n in left) else ""))

        home = prefsseed.prefix_of(source.path)
        pids = probe.running_pids(home) if home else []
        self.running_label.setText(
            "Affinity is running in the source (pid %s). It rewrites "
            "preferences.dat and RecentFiles.xml as it works, so this copies "
            "them as they are this second. Closing it first gives you exactly "
            "what it saves on exit." % pids[0] if pids else "")
        self._letters_for(source)

    # -- drive letters -------------------------------------------------------

    def _letters_for(self, source):
        for box in self.letter_boxes:
            box.setParent(None)
            box.deleteLater()
        self.letter_boxes = []

        home = prefsseed.prefix_of(source.path) if source else None
        letters = prefsseed.drive_letters(home) if home else []
        ready = prefsseed.is_initialised(self.dest_prefix)

        if source is None:
            self.letters_note.setText("")
            return
        if not letters:
            self.letters_note.setText(
                "The source has no drive letters beyond C: and Z:."
                if home else
                "This source is not inside a prefix, so it has no drive "
                "letters to bring.")
            return
        if not ready:
            self.letters_note.setText(
                "This prefix has not been set up by Wine yet, so drive letters "
                "cannot be added: Wine only creates C: when it creates the "
                "drive list itself. Run Setup first, then copy again.")
        else:
            self.letters_note.setText(
                "Recent files and paths inside documents are Windows paths, so "
                "these decide whether they still resolve. Existing letters in "
                "this prefix are never changed.")

        used = source.recents_by_drive()
        existing = {d.letter: d.target for d in prefsseed.drive_letters(self.dest_prefix)} \
            if ready else {}
        for d in letters:
            notes = []
            if used.get(d.label[0]):
                notes.append("%d recent file(s)" % used[d.label[0]])
            if not d.available:
                notes.append("not mounted now")
            clash = existing.get(d.letter)
            if clash == d.target:
                notes.append("already set here")
            elif clash:
                notes.append("already %s here; left alone" % clash)
            box = QCheckBox("%s  →  %s%s" % (d.label, d.target,
                            ("   (" + ", ".join(notes) + ")") if notes else ""))
            box.setChecked(ready and clash is None)
            box.setEnabled(ready and clash is None)
            box.setProperty("letter", d)
            self.letters_layout.addWidget(box)
            self.letter_boxes.append(box)

    def chosen_letters(self):
        return [b.property("letter") for b in self.letter_boxes
                if b.isEnabled() and b.isChecked()]

    # -- the answer ------------------------------------------------------------

    def _confirm(self):
        source = self.picker.currentData()
        if source is None:
            chosen = QFileDialog.getExistingDirectory(
                self, "Prefix or Settings folder to copy from", str(Path.home()))
            if not chosen:
                return
            source = prefsseed.resolve_source(chosen)
            if source is None:
                QMessageBox.warning(
                    self, "Nothing to copy",
                    f"No Affinity settings were found in {chosen}.\n\n"
                    "Choose a prefix, or the Settings folder inside one.")
                return
            self.manual = source
            self._describe(source)
            return              # show what it found before copying it
        self.accept()

    def chosen_source(self):
        return self.manual or self.picker.currentData()

    def mode(self):
        return prefsseed.REPLACE if self.replace.isChecked() else prefsseed.FILL

class RemovalDialog(SizedDialog):
    """Everything removing a prefix would take with it, as a list.

    The confirmation is the list. A yes/no on a summary is where "and 4 other
    items" hides the one you would have objected to -- and the things most
    easily forgotten here are the ones that bite later: a menu entry that
    launches nothing, and a document association that silently stops working
    because the prefix holding it has gone.

    Two rows are not like the others. Settings snapshots are listed unticked,
    because losing the backups of a thing along with the thing is the wrong
    default. And anything without our marker is not offered at all -- it is
    named at the bottom as left alone, so the dialog can say what it will not
    do as well as what it will.
    """

    FIT_MIN_WIDTH = 860
    COLUMNS = ["", "What", "Where", "Size"]

    def __init__(self, parent, plan):
        super().__init__(parent)
        self.setWindowTitle(f"Remove {plan.name}")
        self.setModal(True)
        ui.apply(self)
        self.plan = plan
        self.boxes = []

        layout = QVBoxLayout(self)
        blurb = QLabel(
            f"<b>{plan.name}</b> put all of this on this machine. Choose what "
            "goes.<br><br>The prefix, its log and any snapshots are deleted "
            "outright. The small host-side files — menu entries, document "
            "type definitions, icons — are moved aside rather than deleted, "
            "so those are recoverable; the rest is not.")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 34)
        self.tree.setColumnWidth(1, 300)
        for item in plan.items:
            row = SizeSortItem([
                "", item.label,
                item.detail,
                probe.human_size(item.size) if item.size else "",
            ])
            row.setData(SizeSortItem.SIZE_COLUMN, Qt.ItemDataRole.UserRole,
                        item.size)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked if item.default
                              else Qt.CheckState.Unchecked)
            row.setData(0, Qt.ItemDataRole.UserRole, item)
            self.tree.addTopLevelItem(row)
            self.boxes.append(row)
        # Swapped: the plan's own wording is the reason a row is proposed, and
        # it is more useful than the path for the rows that have no path.
        self.tree.setColumnWidth(2, 360)
        layout.addWidget(self.tree, 1)

        if plan.left_alone:
            left = QLabel(
                "<b>Left alone, not ours:</b> "
                + ", ".join(str(a.path.name if a.path else a.detail)
                            for a in plan.left_alone[:8]))
            left.setWordWrap(True)
            left.setObjectName("descriptionLabel")
            layout.addWidget(left)

        if plan.running:
            running = QLabel(
                f"Affinity is running in this prefix (pid {plan.running[0]}). "
                "Nothing can be removed until it is closed.")
            running.setWordWrap(True)
            running.setObjectName("cautionText")
            layout.addWidget(running)

        self.arm = QCheckBox("Yes, remove the ticked items")
        layout.addWidget(self.arm)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Remove")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.arm.toggled.connect(self._retotal)
        self.tree.itemChanged.connect(lambda *_: self._retotal())
        self._retotal()

    # Removing these actually frees the space. The rest are moved aside by
    # hoststate.delete, which keeps a copy, so counting them in a figure
    # labelled "freed" would be a promise the button does not keep.
    FREES_SPACE = ("prefix", "log", "settings-backup")

    def _retotal(self):
        chosen = self.chosen()
        freed = sum(i.size for i in chosen if i.kind in self.FREES_SPACE)
        label = f"Remove {len(chosen)} item(s)"
        if freed:
            label += f"  (frees {probe.human_size(freed)})"
        self.ok.setText(label if chosen else "Remove")
        self.ok.setEnabled(bool(chosen) and self.arm.isChecked()
                           and not self.plan.running)

    def chosen(self):
        return [row.data(0, Qt.ItemDataRole.UserRole) for row in self.boxes
                if row.checkState(0) == Qt.CheckState.Checked]


class SnapshotsDialog(SizedDialog):
    """Settings snapshots for one prefix: take, restore, delete.

    A snapshot is the prefix's configuration and not the prefix -- tens of
    kilobytes against several gigabytes -- which is what makes it something to
    take before every risky change rather than once a year.

    Restoring across prefixes is allowed and is the point: it is how the live
    prefix's settings get into a test one. So the confirmation names both sides
    and says when they differ, because restoring into the wrong prefix is the
    expensive mistake."""

    FIT_MIN_WIDTH = 760
    COLUMNS = ["Taken", "Label", "From", "Files", "Size"]

    def __init__(self, parent, entry):
        super().__init__(parent)
        self.entry = entry
        self.name = entry["name"]
        self.path = Path(entry["path"])
        self.setWindowTitle(f"Settings snapshots — {self.name}")
        self.setModal(True)
        ui.apply(self)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Preferences, shortcuts, recent files and the session state — "
            "copied aside under a dated name, and kept as a directory so you "
            "can read one without restoring it.")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.tree, 1)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("descriptionLabel")
        layout.addWidget(self.detail)

        row = QHBoxLayout()
        take = QPushButton("Take a snapshot…")
        take.clicked.connect(self._take)
        row.addWidget(take)
        row.addStretch(1)
        self.restore_button = QPushButton("Restore…")
        self.restore_button.clicked.connect(self._restore)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for b in (self.restore_button, self.delete_button, close):
            row.addWidget(b)
        layout.addLayout(row)

        self._reload()

    # -- the list ------------------------------------------------------------

    def _reload(self):
        self.tree.clear()
        for snap in snapshots.listing(self.name):
            item = QTreeWidgetItem([
                snap.when,
                snap.label or "—",
                snap.prefix,
                str(snap.files),
                probe.human_size(snap.bytes),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, snap)
            self.tree.addTopLevelItem(item)
        for i in range(len(self.COLUMNS)):
            self.tree.resizeColumnToContents(i)
        self._selection_changed()

    def selected(self):
        rows = self.tree.selectedItems()
        return rows[0].data(0, Qt.ItemDataRole.UserRole) if rows else None

    def _selection_changed(self):
        snap = self.selected()
        self.restore_button.setEnabled(snap is not None)
        self.delete_button.setEnabled(snap is not None)
        if snap is None:
            self.detail.setText(
                "Nothing here yet." if not self.tree.topLevelItemCount()
                else "Choose one to restore or delete it.")
            return
        self.detail.setText(f"{snap.path}\n" + " ".join(snap.notes))

    # -- taking --------------------------------------------------------------

    def _take(self):
        label, ok = QInputDialog.getText(
            self, "Label this snapshot",
            "An optional name, so you can tell it apart later\n"
            "(\"before 3.3\", \"known good\"):")
        if not ok:
            return
        try:
            snap = snapshots.take(self.name, self.path, label)
        except (snapshots.NoSettings, snapshots.NotASnapshot, OSError) as exc:
            QMessageBox.warning(self, "Could not take a snapshot", str(exc))
            return
        self._reload()
        self.detail.setText(f"Took {snap.name} — {snap.files} file(s), "
                            f"{probe.human_size(snap.bytes)}.")

    # -- restoring -----------------------------------------------------------

    def _restore(self):
        snap = self.selected()
        if snap is None:
            return
        try:
            plan = snapshots.plan_restore(snap, self.name, self.path)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot restore", str(exc))
            return

        box = QMessageBox(self)
        box.setWindowTitle("Restore these settings")
        box.setText("\n\n".join(plan.describe()))
        box.setInformativeText(
            "The settings currently in this prefix are snapshotted first, and "
            "the ones they replace are kept beside them with the date in the "
            "name. Nothing is deleted.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        try:
            notes = snapshots.restore(plan)
        except (maintenance.Busy, OSError) as exc:
            QMessageBox.warning(self, "Could not restore", str(exc))
            return
        prefixlog.write(self.name, "Restored settings from " + str(snap))
        self._reload()
        QMessageBox.information(self, "Restored", "\n".join(notes))

    # -- deleting ------------------------------------------------------------

    def _delete(self):
        snap = self.selected()
        if snap is None:
            return
        if QMessageBox.question(
            self, "Delete this snapshot",
            f"Permanently delete {snap.name}?\n\n{snap.path}\n\n"
            "There is no undo, and a snapshot is the only copy of the "
            "preferences it holds.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Ok:
            return
        try:
            snapshots.remove(snap)
        except (snapshots.NotASnapshot, OSError) as exc:
            QMessageBox.warning(self, "Could not delete it", str(exc))
            return
        self._reload()


class SizeSortItem(QTreeWidgetItem):
    """A row whose Size column sorts by bytes, not by the string.

    "9.9M" sorts after "787.2M" alphabetically, which makes a size column that
    cannot do the one thing it is for."""

    SIZE_COLUMN = 1

    def __lt__(self, other):
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if column == self.SIZE_COLUMN:
            return (self.data(column, Qt.ItemDataRole.UserRole) or 0) < \
                   (other.data(column, Qt.ItemDataRole.UserRole) or 0)
        return super().__lt__(other)


class CleanDialog(SizedDialog):
    FIT_MIN_WIDTH = 760

    """What can go, with what it costs, chosen item by item.

    Presented rather than decided. A Wine build this prefix is not running is
    still a build someone kept on purpose -- the sep04 build in the project's
    own prefix was there precisely as a fallback -- so nothing is ticked by
    default except what is unambiguously spoil: archives already unpacked,
    saved-aside copies, and temporary files."""

    SAFE_BY_DEFAULT = {"archive", "temp", "backup"}
    COLUMNS = ["Item", "Size", "What it is"]

    def __init__(self, parent, name, items):
        super().__init__(parent)
        self.setWindowTitle(f"Clean {name}")
        self.setModal(True)
        ui.apply(self)
        self.items = items

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Nothing here is needed to run Affinity. Wine builds are offered "
            "only when this prefix is neither running them nor pointing a "
            "launcher or its ElementalWarriorWine symlink at them.\n\n"
            "Click a column heading to sort."
        )
        blurb.setWordWrap(True)
        blurb.setObjectName("descriptionLabel")
        layout.addWidget(blurb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumHeight(300)

        for item in items:
            row = SizeSortItem([item.path.name, probe.human_size(item.size), item.reason])
            row.setData(1, Qt.ItemDataRole.UserRole, item.size)
            row.setData(0, Qt.ItemDataRole.UserRole, item)
            row.setToolTip(0, str(item.path))
            row.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.setCheckState(
                0,
                Qt.CheckState.Checked if item.kind in self.SAFE_BY_DEFAULT
                else Qt.CheckState.Unchecked,
            )
            self.tree.addTopLevelItem(row)

        # Largest first: what is worth reclaiming is the reason the dialog is open.
        self.tree.sortItems(1, Qt.SortOrder.DescendingOrder)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(lambda *_: self._retotal())
        layout.addWidget(self.tree, 1)

        row = QHBoxLayout()
        all_button = QPushButton("Select all")
        none_button = QPushButton("Select none")
        safe_button = QPushButton("Select the safe ones")
        safe_button.setToolTip(
            "Archives, saved-aside copies and temporary files -- everything "
            "except the Wine builds.")
        all_button.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        none_button.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        safe_button.clicked.connect(self._select_safe)
        row.addWidget(all_button)
        row.addWidget(none_button)
        row.addWidget(safe_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.total = QLabel()
        self.total.setObjectName("descriptionLabel")
        layout.addWidget(self.total)
        self._retotal()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Remove selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _rows(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _set_all(self, state):
        self.tree.blockSignals(True)
        for row in self._rows():
            row.setCheckState(0, state)
        self.tree.blockSignals(False)
        self._retotal()

    def _select_safe(self):
        self.tree.blockSignals(True)
        for row in self._rows():
            item = row.data(0, Qt.ItemDataRole.UserRole)
            row.setCheckState(
                0,
                Qt.CheckState.Checked if item.kind in self.SAFE_BY_DEFAULT
                else Qt.CheckState.Unchecked,
            )
        self.tree.blockSignals(False)
        self._retotal()

    def _retotal(self):
        chosen = self.selected()
        total = sum(i.size for i in chosen)
        builds = sum(1 for i in chosen if i.kind == "wine-build")
        warn = f"   -- including {builds} Wine build(s)" if builds else ""
        self.total.setText(
            f"{len(chosen)} of {len(self.items)} selected, "
            f"{probe.human_size(total)}{warn}")

    def selected(self):
        return [
            row.data(0, Qt.ItemDataRole.UserRole)
            for row in self._rows()
            if row.checkState(0) == Qt.CheckState.Checked
        ]


class CollisionDialog(SizedDialog):
    FIT_MIN_WIDTH = 620

    """What to do about a directory that is already there.

    Offered rather than decided: only the user knows whether what is sitting at
    that path matters. Nothing here deletes anything -- "use it" installs
    alongside what is present and "rename the existing one" moves it aside,
    because what is usually in the way is a Wine prefix of several gigabytes
    that took an hour to build."""

    USE = "use"
    RENAME_OLD = "rename_old"
    RENAME_NEW = "rename_new"

    def __init__(self, parent, path, info):
        super().__init__(parent)
        self.choice = None
        self.setWindowTitle("That directory already exists")
        self.setModal(True)
        ui.apply(self)

        if info.get("empty"):
            what = "It is empty."
        elif info.get("has_affinity"):
            version = info.get("affinity_version") or "an unknown version"
            what = f"It is a Wine prefix with Affinity {version} installed."
        elif info.get("is_prefix"):
            what = "It is a Wine prefix with no Affinity in it."
        else:
            what = f"It holds {info.get('entry_count', 0)} item(s) and is not a Wine prefix."

        heading = QLabel(str(path))
        heading.setObjectName("sectionTitle")
        heading.setWordWrap(True)

        detail = QLabel(what + "\n\nNothing here deletes anything.")
        detail.setObjectName("descriptionLabel")
        detail.setWordWrap(True)

        use = QPushButton("Use it anyway")
        use.setToolTip("Install into this directory. Existing contents are left in place.")
        use.clicked.connect(lambda: self._pick(self.USE))

        rename_old = QPushButton("Rename the existing one")
        rename_old.setToolTip(f"Move it to {registry.aside_path(path).name}, then install fresh.")
        rename_old.clicked.connect(lambda: self._pick(self.RENAME_OLD))

        rename_new = QPushButton("Choose a different name")
        rename_new.setObjectName("okButton")
        rename_new.clicked.connect(lambda: self._pick(self.RENAME_NEW))

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)

        row = QHBoxLayout()
        for b in (use, rename_old, rename_new):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addLayout(row)

    def _pick(self, choice):
        self.choice = choice
        self.accept()


class NewPrefixDialog(SizedDialog):
    FIT_MIN_WIDTH = 620

    def __init__(self, parent, reg: registry.Registry):
        super().__init__(parent)
        self.reg = reg
        self.setWindowTitle("New managed prefix")
        self.setModal(True)
        ui.apply(self)

        form = QFormLayout()
        self.name = QLineEdit(reg.suggest_name())
        self.name.textChanged.connect(self._name_changed)
        form.addRow("Name", self.name)

        # Read-only: a managed prefix lives directly under the base directory,
        # named after itself. Somewhere else would be an adopted prefix, which
        # is a different action.
        self.path_label = QLabel()
        self.path_label.setObjectName("descriptionLabel")
        self.path_label.setWordWrap(True)
        form.addRow("Directory", self.path_label)

        self.pin = QCheckBox("Install a specific build instead of the current release")
        self.pin.toggled.connect(self._pin_toggled)
        form.addRow("", self.pin)

        prow = QHBoxLayout()
        self.installer_file = QLineEdit()
        self.installer_file.setEnabled(False)
        self.installer_file.setPlaceholderText("Affinity-x64-<version>.exe")
        self.pick = QPushButton("Choose\u2026")
        self.pick.setEnabled(False)
        self.pick.clicked.connect(self._pick_installer)
        prow.addWidget(self.installer_file, 1)
        prow.addWidget(self.pick)
        pholder = QWidget()
        pholder.setLayout(prow)
        form.addRow("Installer", pholder)

        self.note = QLabel(
            "downloads.affinity.studio has no versioned URL, so an unpinned "
            "install always gets the current release. Pin a kept installer to "
            "reproduce an older one."
        )
        self.note.setObjectName("descriptionLabel")
        self.note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Create and install")
        self.ok.setObjectName("okButton")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addWidget(buttons)

        self.result_entry = None
        self._name_changed(self.name.text())

    def _name_changed(self, text):
        try:
            registry.validate_name(text)
            valid = True
        except registry.InvalidName:
            valid = False
        self.ok.setEnabled(valid)
        if valid:
            path = registry.path_for(text)
            exists = path.exists()
            self.path_label.setText(
                str(path) + ("   \u2014 already exists" if exists else "")
            )
        else:
            self.path_label.setText(
                "Letters, digits, space, dot, underscore or hyphen; "
                "1-64 characters, starting with a letter or digit."
            )

    def _pin_toggled(self, on):
        self.installer_file.setEnabled(on)
        self.pick.setEnabled(on)

    def _pick_installer(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Affinity installer",
            str(Path.home()),
            "Installers (*.exe);;All files (*)",
        )
        if chosen:
            self.installer_file.setText(chosen)

    def _accept(self):
        name = self.name.text().strip()
        path = registry.path_for(name)
        pinned = self.installer_file.text().strip() if self.pin.isChecked() else None

        if pinned and not Path(pinned).expanduser().is_file():
            QMessageBox.warning(self, "Installer not found", f"{pinned} does not exist.")
            return

        if path.exists():
            info = registry.describe_collision(path)
            managed = self.reg.by_path(path)
            if managed:
                QMessageBox.warning(
                    self,
                    "Already managed",
                    f"{path} is already managed as {managed['name']!r}.\n\n"
                    "Pick a different name.",
                )
                return
            dialog = CollisionDialog(self, path, info)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.choice:
                return
            if dialog.choice == CollisionDialog.RENAME_NEW:
                self.name.setFocus()
                self.name.selectAll()
                return
            if dialog.choice == CollisionDialog.RENAME_OLD:
                try:
                    moved = registry.rename_aside(path)
                except OSError as e:
                    QMessageBox.critical(self, "Could not rename", str(e))
                    return
                QMessageBox.information(
                    self, "Renamed", f"The existing directory is now:\n{moved}"
                )

        try:
            self.result_entry = self.reg.add(name, path, installer_file=pinned)
        except (registry.InvalidName, registry.DuplicateName, registry.PathInUse) as e:
            QMessageBox.warning(self, "Cannot add this prefix", str(e))
            return
        self.accept()


class CommandsDialog(SizedDialog):
    FIT_MIN_WIDTH = 900

    """Every way this prefix can be run -- copy it, run it, or put it in the menu.

    A prefix usually holds several Wine builds and three ways to start Affinity,
    and the difference between them is the difference between a meaningful test
    and a misleading one. Listing them beats remembering them."""

    def __init__(self, parent, entry):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle(f"Commands — {entry['name']}")
        self.setModal(False)
        ui.apply(self)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Configuration", "In menu"])
        self.tree.setColumnWidth(0, 620)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)

        self.detail = QLabel("")
        self.detail.setObjectName("descriptionLabel")
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(52)

        self.command_line = QLineEdit()
        self.command_line.setReadOnly(True)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_selected)
        self.run_button = QPushButton("Run")
        self.run_button.setObjectName("okButton")
        self.run_button.clicked.connect(self.run_selected)
        self.menu_button = QPushButton("Add to menu")
        self.menu_button.clicked.connect(self.menu_selected)
        self.unmenu_button = QPushButton("Remove from menu")
        self.unmenu_button.clicked.connect(self.remove_menu_entry)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        row = QHBoxLayout()
        for b in (self.copy_button, self.run_button, self.menu_button, self.unmenu_button):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.detail)
        layout.addWidget(self.command_line)
        layout.addLayout(row)

        self.reload()

    def reload(self):
        self.tree.clear()
        self.commands = commands_mod.grouped(self.entry["path"])
        for group, items in self.commands.items():
            parent = QTreeWidgetItem([group, ""])
            parent.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(parent)
            for command in items:
                label = command.label + ("   (careful)" if command.risky else "")
                in_menu = "yes" if desktopentry.exists(self.entry["name"], command) else ""
                child = QTreeWidgetItem([label, in_menu])
                child.setData(0, Qt.ItemDataRole.UserRole, command)
                parent.addChild(child)
            parent.setExpanded(True)
        self._selection_changed()

    def selected_command(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _selection_changed(self):
        command = self.selected_command()
        enabled = command is not None
        for b in (self.copy_button, self.run_button, self.menu_button):
            b.setEnabled(enabled)
        if not command:
            self.detail.setText("")
            self.command_line.clear()
            self.unmenu_button.setEnabled(False)
            return
        self.detail.setText(command.detail)
        self.command_line.setText(command.shell)
        in_menu = desktopentry.exists(self.entry["name"], command)
        self.menu_button.setText("Edit menu entry" if in_menu else "Add to menu")
        self.unmenu_button.setEnabled(in_menu)

    def _double_clicked(self, item, _column):
        command = item.data(0, Qt.ItemDataRole.UserRole)
        # Double-click runs, except for the ones that end a session -- those
        # deserve a deliberate click on Run.
        if command and not command.risky:
            self.run_selected()

    def copy_selected(self):
        command = self.selected_command()
        if not command:
            return
        QApplication.clipboard().setText(command.shell)
        self.detail.setText("Copied to the clipboard.")

    def run_selected(self):
        command = self.selected_command()
        if not command:
            return
        if command.risky:
            reply = QMessageBox.question(
                self,
                command.label,
                f"{command.detail}\n\nRun it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        if "Affinity" in command.group and not command.risky:
            pids = probe.running_pids(self.entry["path"])
            if pids:
                QMessageBox.information(
                    self,
                    "Already running",
                    f"Affinity is already running in this prefix (pid {pids[0]}).\n\n"
                    "A second instance kills the first, so this was not started.",
                )
                return
        try:
            command.run()
        except OSError as e:
            QMessageBox.critical(self, "Could not run", str(e))
            return
        self.detail.setText(f"Started: {command.label}")

    def menu_selected(self):
        command = self.selected_command()
        if not command:
            return
        existing = desktopentry.read_name(self.entry["name"], command)
        suggested = existing or f"{command.label} ({self.entry['name']})"
        name, ok = _ask_name(self, suggested, title="Menu entry name", label="Shows in the menu as")
        if not ok or not name.strip():
            return
        try:
            path = desktopentry.write(self.entry["name"], command, name=name.strip())
        except (OSError, FileExistsError) as e:
            QMessageBox.critical(self, "Could not write the menu entry", str(e))
            return
        self.detail.setText(f"Menu entry written: {path}")
        self.reload()

    def remove_menu_entry(self):
        command = self.selected_command()
        if not command:
            return
        try:
            removed = desktopentry.remove(self.entry["name"], command)
        except (OSError, PermissionError) as e:
            QMessageBox.critical(self, "Could not remove the menu entry", str(e))
            return
        self.detail.setText("Menu entry removed." if removed else "There was no menu entry.")
        self.reload()


class FindDialog(SizedDialog):
    FIT_MIN_WIDTH = 940

    """Wine prefixes on this machine, and what to do with them.

    Unfiltered on purpose. An earlier version listed only prefixes with
    Affinity.exe in them, which meant the one prefix somebody most needed help
    with -- the one whose install was interrupted -- was the one the manager
    said it could not find. Everything found is listed, with what it is in the
    Affinity column, and only the complete ones are ticked to begin with.

    "Open folder" is here for the same reason. Deciding whether a path from a
    search is the prefix you think it is means looking inside it, and that
    should not require closing the dialog.
    """

    def __init__(self, parent, reg: registry.Registry):
        super().__init__(parent)
        self.reg = reg
        self.manager = parent
        self.setWindowTitle("Find existing installations")
        self.setModal(True)
        ui.apply(self)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["", "Name to use", "Affinity", "Wine", "Path"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 34)
        self.tree.setColumnWidth(1, 220)
        self.tree.itemSelectionChanged.connect(self._selection_changed)

        self.open_button = QPushButton("Open folder")
        self.open_button.setToolTip(
            "Show the selected prefix in the file manager, before deciding "
            "anything about it")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_selected)

        self.move_box = QCheckBox("Move them into the base directory")
        self.move_box.setToolTip(
            "A prefix is relocatable -- dosdevices/c: is relative and the "
            "registries hold no absolute prefix paths. Absolute symlinks "
            "inside it are repointed, and any launcher or desktop entry naming "
            "the old path is rewritten, with a copy of each kept beside it."
        )

        self.status = QLabel("Searching…")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)

        self.adopt_button = QPushButton("Manage the ticked ones")
        self.adopt_button.setObjectName("okButton")
        self.adopt_button.clicked.connect(self._adopt)
        self.adopt_button.setEnabled(False)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(self.move_box)
        row.addStretch(1)
        row.addWidget(self.open_button)
        row.addWidget(self.adopt_button)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.found = []
        # Searched after the dialog is on screen, not before it exists. The
        # scan walks the home directory, /mnt, /opt and the removable-media
        # roots, and running it here meant several seconds in which the
        # application had simply stopped, with the window that explains what
        # is happening still unbuilt.
        self.status.setText("Searching…")

    @staticmethod
    def _what_it_is(item) -> str:
        """The Affinity column, for a row that may have no Affinity in it."""
        if item.get("has_affinity"):
            return item["affinity_version"] or "installed"
        if item.get("wine"):
            return "not installed yet"
        return "empty prefix"

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_searched", False):
            return
        self._searched = True
        QTimer.singleShot(0, self._search)

    def _search(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._do_search()
        finally:
            QApplication.restoreOverrideCursor()

    def _do_search(self):
        self.found = discover.find_installations()
        self.tree.clear()
        new = incomplete = 0
        for item in self.found:
            managed = item["managed_as"]
            row = QTreeWidgetItem(
                [
                    "",
                    managed or item["suggested_name"],
                    self._what_it_is(item),
                    item["wine"] or "—",
                    item["path"],
                ]
            )
            if managed:
                # Not tickable, but still selectable. setDisabled() was the
                # obvious way to say "nothing to do here" and took the row out
                # of selection with it -- so "Open folder" went dead on exactly
                # the prefixes somebody is most likely to want to look inside.
                row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                row.setText(1, f"{managed}  (already managed)")
                dim = QBrush(QColor("#8a8496"))
                for column in range(self.tree.columnCount()):
                    row.setForeground(column, dim)
            else:
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsEditable)
                # Complete installs start ticked. An incomplete one is listed
                # and tickable but not chosen for the user: adding a half-built
                # prefix to the list is a decision, not a default.
                row.setCheckState(0, Qt.CheckState.Checked if item.get("has_affinity")
                                  else Qt.CheckState.Unchecked)
                new += 1
                if not item.get("has_affinity"):
                    incomplete += 1
            row.setData(0, Qt.ItemDataRole.UserRole, item)
            self.tree.addTopLevelItem(row)
        self.adopt_button.setEnabled(new > 0)
        note = ""
        if incomplete:
            note = (f" {incomplete} of them has no Affinity installed yet — an "
                    "interrupted install looks like that, and managing it lets "
                    "Setup finish the job." if incomplete == 1 else
                    f" {incomplete} of them have no Affinity installed yet — an "
                    "interrupted install looks like that, and managing one lets "
                    "Setup finish the job.")
        self.status.setText(
            f"{len(self.found)} prefix(es) found, {new} not yet managed.{note} "
            "Names are editable — double-click one. Ticked entries are added to "
            "the list; nothing is moved unless you ask."
        )
        self._selection_changed()

    def _selection_changed(self):
        self.open_button.setEnabled(bool(self.tree.selectedItems()))

    def _open_selected(self):
        """Show the prefix in the file manager.

        QDesktopServices rather than xdg-open: it is the same mechanism without
        a subprocess, and it honours the desktop's own file manager."""
        rows = self.tree.selectedItems()
        if not rows:
            return
        data = rows[0].data(0, Qt.ItemDataRole.UserRole)
        path = Path(data["path"])
        if not path.is_dir():
            QMessageBox.warning(self, "Not there any more",
                                f"{path} no longer exists.")
            self._search()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self, "Could not open it",
                f"The desktop refused to open {path}. It is still there; open "
                "it by hand if you need to look.")

    def _adopt(self):
        move = self.move_box.isChecked()
        added, problems, moved = 0, [], []
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            data = row.data(0, Qt.ItemDataRole.UserRole)
            if data["managed_as"] or row.checkState(0) != Qt.CheckState.Checked:
                continue
            name = row.text(1).strip()
            path = Path(data["path"])
            if move:
                # Through coldstart, which is the one place that knows how to
                # move a prefix: a rename where it can be one, no
                # copy-then-delete across filesystems unless it is asked for,
                # absolute symlinks inside repointed, and the launchers that
                # name the old path rewritten with a copy kept beside each.
                # This used to call discover.move_into_base, which did none of
                # that -- it did the copy-then-delete that maintenance.relocate
                # exists to refuse, and left every desktop entry pointing at a
                # directory that had gone.
                try:
                    self.manager.lock.claim(name, f"Move {path.name}")
                except oplock.InUse as exc:
                    problems.append(f"{name}: {exc}")
                    continue
                try:
                    _, notes = coldstart.adopt_by_moving(
                        self.reg, data, name, base=registry.base_dir())
                    added += 1
                    moved += notes
                except (maintenance.NotAPrefix, maintenance.DestinationInUse,
                        maintenance.NotEnoughSpace, maintenance.Busy,
                        maintenance.CloneFailed, registry.InvalidName,
                        registry.DuplicateName, registry.PathInUse, OSError) as e:
                    problems.append(f"{name}: {e}")
                finally:
                    self.manager.lock.release(name)
                continue
            try:
                self.reg.add(name, path)
                added += 1
            except (registry.InvalidName, registry.DuplicateName, registry.PathInUse) as e:
                problems.append(f"{name}: {e}")
        if problems:
            QMessageBox.warning(
                self,
                "Some were skipped" if added else "Nothing was added",
                "\n".join(problems[:12]),
            )
        if moved:
            QMessageBox.information(self, "Moved", "\n".join(moved[:14]))
        if added:
            self.accept()
        else:
            self._search()


class SettingsDialog(SizedDialog):
    FIT_MIN_WIDTH = 620

    """Where prefixes live, and which theme to wear.

    The base directory defaults to ~/.AffinityLinuxManager and is changed here
    rather than demanded on first run -- most people never move it, and an
    install that opens with a directory chooser is an install that gets
    cancelled."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        ui.apply(self)

        self._original_base = registry.base_dir()
        self._original_theme = ui.current_theme()
        self.base_changed = False
        self.theme_changed = False

        form = QFormLayout()

        row = QHBoxLayout()
        self.base = QLineEdit(str(self._original_base))
        browse = QPushButton("Browse…")
        browse.setObjectName("actionButton")
        browse.clicked.connect(self._browse)
        row.addWidget(self.base, 1)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Base directory", holder)

        row2 = QHBoxLayout()
        self.installer_script = QLineEdit(str(installer.describe().get("script") or ""))
        pick_installer = QPushButton("Browse\u2026")
        pick_installer.setObjectName("actionButton")
        pick_installer.clicked.connect(self._pick_installer)
        fetch = QPushButton("Fetch branch\u2026")
        fetch.setObjectName("actionButton")
        fetch.setToolTip(
            f"Clone or update {installer.PREFERRED_BRANCH} into the Manager "
            "directory and use that, so installs do not depend on a development "
            "checkout being on the right branch."
        )
        fetch.clicked.connect(self._fetch_branch)
        row2.addWidget(self.installer_script, 1)
        row2.addWidget(pick_installer)
        row2.addWidget(fetch)
        holder2 = QWidget()
        holder2.setLayout(row2)
        form.addRow("AffinityOnLinux installer", holder2)

        self.installer_state = QLabel(installer.summary())
        self.installer_state.setObjectName("descriptionLabel")
        self.installer_state.setWordWrap(True)
        form.addRow("", self.installer_state)

        self.theme = QComboBox()
        for name in ui.THEMES:
            self.theme.addItem(ui.DISPLAY_NAMES[name], name)
        self.theme.setCurrentIndex(ui.THEMES.index(self._original_theme))
        form.addRow("Theme", self.theme)

        note = QLabel(
            f"Prefixes are created as subdirectories of the base directory, "
            f"alongside a '{registry.MANAGER_SUBDIR}' subdirectory holding this "
            "application's metadata. Prefixes already managed keep their own "
            "paths — changing this only affects new ones. Themes come from the "
            "AffinityOnLinux installer, so the two match."
        )
        note.setObjectName("descriptionLabel")
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Base directory for managed prefixes", self.base.text() or str(Path.home())
        )
        if chosen:
            self.base.setText(chosen)

    def _pick_installer(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "AffinityLinuxInstaller.py",
            self.installer_script.text() or str(Path.home()),
            "Python (*.py);;All files (*)",
        )
        if chosen:
            self.installer_script.setText(chosen)
            self._restate()

    def _fetch_branch(self):
        lines = []
        for line in installer.fetch_managed_checkout():
            lines.append(line)
        QMessageBox.information(self, "Fetch branch", "\n".join(lines) or "Nothing happened.")
        from affinity_manager import settings

        fetched = settings.get("installer_script")
        if fetched:
            self.installer_script.setText(fetched)
        self._restate()

    def _restate(self):
        path = self.installer_script.text().strip()
        info = installer.describe(Path(path)) if path else installer.describe()
        self.installer_state.setText(installer.summary(info))

    def _accept(self):
        chosen = Path(self.base.text().strip()).expanduser() if self.base.text().strip() else registry.DEFAULT_BASE
        if chosen != self._original_base:
            try:
                (chosen / registry.MANAGER_SUBDIR).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                QMessageBox.critical(self, "Cannot use this directory", str(e))
                return
            registry.set_base_dir(chosen)
            self.base_changed = True
        script = self.installer_script.text().strip()
        if script:
            from affinity_manager import settings as _settings

            if not Path(script).expanduser().is_file():
                QMessageBox.warning(self, "Installer not found", f"{script} does not exist.")
                return
            _settings.set("installer_script", str(Path(script).expanduser()))

        theme = self.theme.currentData()
        if theme != self._original_theme:
            ui.set_theme(theme)
            self.theme_changed = True
        self.accept()


class ManagerWindow(QMainWindow):
    COLUMNS = ["Name", "Locked", "Affinity", "Wine", "Size", "State", "Path"]

    def __init__(self):
        super().__init__()
        self.reg = registry.Registry()
        self.probe_thread = None
        # Setup pages, one per prefix, built on first entry. Kept rather than
        # rebuilt because building one constructs the whole installer window;
        # bounded in _drop_spare_setup_pages, because keeping every prefix's is
        # not the same thing as keeping the useful ones.
        self._setup_pages = {}
        # One operation at a time, across every prefix. See oplock: two
        # prefixes provisioning at once collide in the distro package manager
        # whatever this application believes, so this is a correctness matter
        # and not a preference.
        self.lock = oplock.OperationLock()
        self.setWindowTitle(f"Affinity on Linux Manager {__version__}")
        self.resize(1080, 560)
        ui.apply(self)
        self._build()
        self._ensure_manager_dir()
        self.refresh()
        # The watchdog. Every release ought to be explicit and in this file it
        # is, but a hosted installer's work runs in daemon threads that can die
        # without reaching any release, so something has to notice. Two seconds
        # is also what keeps the elapsed time in the caution line moving.
        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(2000)
        self._lock_timer.timeout.connect(self._lock_tick)
        self._lock_timer.start()
        # After the window is up, so these have something to appear over and
        # the list behind them already shows what they name.
        QTimer.singleShot(0, self._report_interrupted)
        QTimer.singleShot(0, self._first_run)

    # ── the first run ────────────────────────────────────────────────────────

    def _first_run(self):
        """Offer something sensible when the prefix list is empty.

        Once, and then not again: coldstart records that the offer was made, so
        a machine-wide walk does not come back every time somebody forgets
        their last prefix. Nothing here acts on its own either -- an Affinity
        install that predates the manager is somebody's working setup, and the
        manager noticing it is not permission to change it."""
        if self.reg.entries:
            return
        self.status.setText("Looking for Affinity installations…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # Painted before the walk, not after it. Both of the lines above only
        # schedule; without this the cursor and the sentence appear when the
        # search that they are there to explain has already finished.
        QApplication.processEvents()
        try:
            situation = coldstart.look(self.reg)
        except OSError as exc:
            self.status.setText(f"Could not search for installations: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        if situation.state == coldstart.FRESH:
            # Also recorded. Nothing was found, and walking the machine again
            # on every start in case something appears is not a trade worth
            # making -- "Find installations" is one click and is what somebody
            # who has just installed Affinity elsewhere would reach for.
            coldstart.mark_asked()
            self.status.setText(
                "No Affinity installation found. 'New prefix' creates one under "
                f"{situation.base}; 'Adopt existing' takes over one this search "
                "did not reach.")
            return
        if situation.state != coldstart.ADOPTABLE:
            return
        self._offer_adoption(situation)

    def _offer_adoption(self, situation):
        # Recorded before the answer, not after: the point is that the offer
        # was made. Whatever the user decides -- including "decide later", and
        # including closing the window -- this must not reappear on the next
        # start, and must not re-arm when the last prefix is forgotten.
        coldstart.mark_asked()
        dialog = FirstRunDialog(self, situation)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status.setText(
                f"{len(situation.installs)} installation(s) found and left alone. "
                "'Find installations' offers them again.")
            return
        name = dialog.chosen_name()
        install = dialog.install

        if dialog.choice == dialog.IN_PLACE:
            try:
                coldstart.adopt_in_place(self.reg, install, name)
            except (registry.InvalidName, registry.DuplicateName,
                    registry.PathInUse) as exc:
                QMessageBox.warning(self, "Cannot manage this prefix", str(exc))
                return
            self.refresh()
            self.status.setText(f"{name} is managed where it is: {install['path']}")
            return

        try:
            self._busy_start(f"Moving {install['path']}", 100,
                             prefix=name, operation="Move into the base directory")
        except oplock.InUse as exc:
            self._refuse(exc)
            return
        try:
            entry, notes = coldstart.adopt_by_moving(
                self.reg, install, name, base=situation.base,
                progress=lambda pct: self._busy_step(pct, f"Moving {name} — {pct}%"))
        except (maintenance.NotAPrefix, maintenance.DestinationInUse,
                maintenance.NotEnoughSpace, maintenance.Busy,
                maintenance.CloneFailed, registry.InvalidName,
                registry.DuplicateName, registry.PathInUse) as exc:
            self._busy_done("Move failed")
            QMessageBox.critical(self, "Could not move it", str(exc))
            return
        self._busy_done(f"{name} moved to {entry['path']}")
        desktopentry.refresh_menu()
        self.refresh()
        QMessageBox.information(
            self, "Moved", f"{name} is now at {entry['path']}.\n\n"
            + "\n".join(notes[:10]))

    def copy_settings_selected(self):
        entry = self.selected_entry()
        if entry:
            self.offer_settings_copy(entry)

    def offer_settings_copy(self, entry):
        """Bring settings, workspaces, recents and drive letters into a prefix.

        Refused while Affinity runs in the DESTINATION -- it would overwrite
        what was copied on exit. A running SOURCE is only a caution: reading it
        is harmless, it just may not be the last word."""
        path = Path(entry["path"])
        pids = probe.running_pids(path) if path.exists() else []
        if pids:
            QMessageBox.warning(
                self, "Affinity is running there",
                f"Affinity is running in {entry['name']} (pid {pids[0]}). Close "
                "it first: it would write its own settings over these when it "
                "exits.")
            return

        destination = prefsseed.destination_for(path)
        others = [e["path"] for e in self.reg.entries if e["name"] != entry["name"]]
        found = prefsseed.sources(others, exclude=path)

        dialog = CarrySettingsDialog(self, path, destination, found)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source = dialog.chosen_source()
        if source is None:
            return
        letters = dialog.chosen_letters()

        try:
            self._busy_start(f"Copying settings into {entry['name']}",
                             prefix=entry["name"], operation="Copy settings")
        except oplock.InUse as exc:
            self._refuse(exc)
            return
        try:
            result = prefsseed.seed(source, destination, mode=dialog.mode())
        except OSError as exc:
            self._busy_done("Copying settings failed", owner=entry["name"])
            # In REPLACE mode the old settings have already been renamed aside
            # by now, so the prefix has no Settings folder at all and the only
            # copy is under a name the user has never seen.
            aside = prefsseed.aside_of(destination)
            where = (f"\n\nThe settings that were there are at {aside}. "
                     "Rename it back to 'Settings' to undo this."
                     if aside else "")
            QMessageBox.critical(self, "Could not copy the settings",
                                 f"{exc}{where}")
            return

        drive_notes = []
        if letters:
            try:
                drive_notes = prefsseed.import_drive_letters(path, letters)
            except prefsseed.PrefixNotReady as exc:
                drive_notes = [str(exc)]

        prefixlog.write(entry["name"],
                        "Settings copied from %s: %d added, %d replaced, %d kept; "
                        "also %s; drive letters: %s"
                        % (source.path, len(result.added), len(result.replaced),
                           len(result.kept), ", ".join(result.extras) or "nothing",
                           "; ".join(drive_notes) or "none"))
        self._busy_done(f"Settings copied into {entry['name']}", owner=entry["name"])

        lines = [f"{len(result.added)} added and {len(result.replaced)} replaced "
                 f"in {destination}."]
        if result.extras:
            lines.append("Also copied: " + ", ".join(result.extras) + ".")
        if result.saved_aside:
            lines.append(f"What was there is kept at {result.saved_aside.name}.")
        if drive_notes:
            lines.append("Drive letters:\n  " + "\n  ".join(drive_notes))
        QMessageBox.information(self, "Settings copied", "\n\n".join(lines))

    # ── recovery ─────────────────────────────────────────────────────────────

    def _report_interrupted(self):
        """Say what did not finish, once, at startup.

        Anything still marked working was written by a manager that is gone --
        single-instance checking established that before this window existed --
        so the operation it was tracking cannot still be running. Recovering it
        quietly would be the easy thing and the wrong one: an interrupted
        install leaves a prefix that looks finished, and the only warning
        anybody gets is this."""
        changed = self.reg.recover()
        if not changed:
            return
        self.refresh()

        blocks = []
        for entry in changed:
            condition = prefixstate.classify(entry["path"], entry=entry)
            lines = [f"<b>{entry['name']}</b> — {condition.detail}"]
            if condition.suggestion:
                lines.append(condition.suggestion)
            tail = self._log_tail(entry)
            if tail:
                lines.append(f"<pre>{tail}</pre>")
            blocks.append("<br>".join(lines))

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unfinished work")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"{len(changed)} prefix{'es' if len(changed) > 1 else ''} had an "
            "operation running when the manager last closed."
        )
        box.setInformativeText("<br><br>".join(blocks))
        ui.apply(box)
        box.exec()

    @staticmethod
    def _log_tail(entry, limit=1200):
        """What the log said after the operation began.

        log_offset was recorded when it started, so this is the part that
        matters rather than the end of a file that may have scrolled past it.
        Returns nothing when there is no log yet, which is the normal case
        until per-prefix logs exist."""
        path = entry.get("log")
        offset = entry.get("log_offset")
        if not path or offset is None:
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                text = f.read(limit).strip()
        except OSError:
            return ""
        return text

    def _ensure_manager_dir(self):
        """Create <base>/Manager up front so the base directory looks managed
        before anything is installed, and so a base on an unwritable disk fails
        here rather than at the end of an install."""
        try:
            registry.manager_dir().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Cannot use this base directory",
                f"{registry.base_dir()} could not be prepared:\n{e}",
            )

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.base_changed:
            # Entries hold absolute paths, so prefixes registered under the old
            # base keep working; only new ones land under the new one.
            self.reg = registry.Registry()
            self._ensure_manager_dir()
        if dialog.theme_changed:
            self.apply_theme()
        self._check_installer()
        self.refresh()

    def apply_theme(self):
        ui.apply(self)
        # A dynamic-property selector is resolved when the widget is polished,
        # so a restyle after the fact needs the polish redone by hand.
        for button in self.findChildren(QPushButton):
            if button.property("class"):
                button.style().unpolish(button)
                button.style().polish(button)
        self.theme_button.setText(ui.DISPLAY_NAMES[ui.next_theme()])

    def cycle_theme(self):
        ui.set_theme(ui.next_theme())
        self.apply_theme()

    # ── the installer's own shape ────────────────────────────────────────────
    #
    # A top bar, then a content area split left-to-right: grouped cards of
    # stacked full-width actions on the left, the thing being worked on in the
    # middle. The button styles were drawn for exactly this -- #actionButton is
    # a 100px-radius pill with left-aligned text and generous padding, which
    # reads as an oversized lozenge in a toolbar row and as a proper menu button
    # stacked in a card.

    @staticmethod
    def _primary(button):
        """Mark a button as the accent action.

        The theme selects on QPushButton#actionButton[class="primary"], a Qt
        dynamic property rather than an object name. Set before the widget is
        shown, as the installer does; changing it later needs an unpolish and
        polish to take effect."""
        button.setObjectName("actionButton")
        button.setProperty("class", "primary")
        return button

    def _action(self, label, slot, description=None, primary=False):
        button = QPushButton(label)
        button.setObjectName("actionButton")
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(44)
        button.clicked.connect(slot)
        if description:
            button.setToolTip(description)
        if primary:
            self._primary(button)
        return button

    def _button_card(self, title, actions):
        """One grouped card of stacked actions, in the installer's vocabulary.

        Built here rather than borrowed: create_button_group is tangled with the
        installer's icon loading and its own label bookkeeping, while the object
        names -- which is what the theme actually styles -- are free to use."""
        card = QFrame()
        card.setObjectName("buttonCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        for button in actions:
            layout.addWidget(button)
        return card

    def _build(self):
        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── top bar ──────────────────────────────────────────────────────────
        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 10, 20, 10)

        title = QLabel("Affinity on Linux Manager")
        title.setObjectName("titleLabel")
        top_layout.addWidget(title)
        top_layout.addStretch(1)

        settings_button = QPushButton("Settings")
        settings_button.setObjectName("zoomButton")
        settings_button.setMinimumHeight(30)
        settings_button.clicked.connect(self.open_settings)
        top_layout.addWidget(settings_button)

        self.theme_button = QPushButton(ui.DISPLAY_NAMES[ui.next_theme()])
        self.theme_button.setObjectName("themeToggle")
        self.theme_button.setMinimumHeight(30)
        self.theme_button.setToolTip("Switch theme")
        self.theme_button.clicked.connect(self.cycle_theme)
        top_layout.addWidget(self.theme_button)

        main_layout.addWidget(top_bar)

        # ── content: cards on the left, the prefixes in the middle ───────────
        content = QWidget()
        content.setObjectName("contentArea")
        content_layout = QHBoxLayout(content)
        content_layout.setSpacing(20)
        content_layout.setContentsMargins(20, 20, 20, 20)

        self.new_button = self._action(
            "New prefix", self.new_prefix,
            "Create a prefix under the base directory and install into it", primary=True)
        self.find_button = self._action(
            "Find installations", self.find_installations,
            "Search this machine for Affinity installations already present")
        self.adopt_button = self._action(
            "Adopt existing", self.adopt,
            "Manage an install already on disk, wherever it is")

        self.launch_button = self._action(
            "Launch", self.launch_selected, "Start Affinity in the selected prefix")
        self.commands_button = self._action(
            "Commands", self.show_commands,
            "Every way this prefix can be run -- copy, run, or add to the menu")
        self.installer_button = self._action(
            "Setup", self.open_installer,
            "Install, update and configure this prefix -- AffinityOnLinux, "
            "inside this window")
        self.copy_settings_button = self._action(
            "Copy settings…", self.copy_settings_selected,
            "Bring preferences, workspaces, recent files and drive letters in "
            "from another prefix. Use after Setup, before first launch")
        self.snapshots_button = self._action(
            "Snapshots", self.show_snapshots,
            "Dated copies of this prefix's preferences, shortcuts and recent "
            "files -- take one before anything risky")
        self.protect_button = self._action(
            "Protect", self.toggle_protection, "Mark this prefix as not deletable")
        self.forget_button = self._action(
            "Stop managing", self.forget_selected,
            "Remove from the list. The directory is left alone")
        self.clone_button = self._action(
            "Clone", self.clone_selected,
            "Copy this prefix to a new one, optionally dropping the Wine builds it is not using")
        self.clean_button = self._action(
            "Clean", self.clean_selected,
            "Reclaim space: Wine builds this prefix does not use, downloaded archives, saved-aside copies")
        self.delete_button = self._action(
            "Delete", self.delete_selected, "Permanently delete this prefix")

        self.refresh_button = self._action(
            "Refresh", self.refresh, "Re-read the registry and re-inspect every prefix")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setSpacing(16)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._button_card(
            "Prefixes", [self.new_button, self.find_button, self.adopt_button]))
        left_layout.addWidget(self._button_card(
            "Selected prefix",
            [self.launch_button, self.commands_button, self.installer_button,
             self.copy_settings_button, self.snapshots_button,
             self.clone_button, self.clean_button,
             self.protect_button, self.forget_button, self.delete_button]))
        left_layout.addWidget(self._button_card("View", [self.refresh_button]))
        left_layout.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setObjectName("leftScroll")
        left_scroll.setWidget(left)
        left_scroll.setMaximumWidth(340)
        content_layout.addWidget(left_scroll, 2)

        # ── the list, under a status card ────────────────────────────────────
        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setSpacing(12)
        middle_layout.setContentsMargins(0, 0, 0, 0)

        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(6)

        status_title = QLabel("Managed prefixes")
        status_title.setObjectName("statusTitle")
        status_layout.addWidget(status_title)

        self.base_label = QLabel()
        self.base_label.setObjectName("descriptionLabel")
        self.base_label.setWordWrap(True)
        status_layout.addWidget(self.base_label)

        # Its own line rather than the status bar: which installer builds new
        # prefixes is standing information, and the status bar is transient.
        self.installer_label = QLabel()
        self.installer_label.setObjectName("descriptionLabel")
        self.installer_label.setWordWrap(True)
        status_layout.addWidget(self.installer_label)

        middle_layout.addWidget(status_card)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.show_commands())
        self.tree.header().setSectionResizeMode(
            len(self.COLUMNS) - 1, QHeaderView.ResizeMode.Stretch
        )
        middle_layout.addWidget(self.tree, 1)

        # The action line: what is happening, and beside it how far along.
        # Determinate where there is something to count, indeterminate where
        # there is not -- a copy can report bytes, a prefix scan cannot, and a
        # bar that sits still reads as a hung application.
        #
        # Built here, but added at the foot of the window rather than to this
        # page, because it belongs to both states: an install running on the
        # Setup page reports through the same line the list uses, and a status
        # bar that vanished on the way into Setup would be the one moment it
        # was most wanted.
        status_row = QWidget()
        status_row_layout = QHBoxLayout(status_row)
        status_row_layout.setContentsMargins(20, 8, 20, 14)
        status_row_layout.setSpacing(12)

        self.status = QLabel("")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        status_row_layout.addWidget(self.status, 1)

        self.progress = QProgressBar()
        self.progress.setObjectName("busyBar")
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(200)
        self.progress.setMaximumHeight(8)
        self.progress.hide()
        status_row_layout.addWidget(self.progress, 0)

        content_layout.addWidget(middle, 3)
        main_layout.addWidget(content, 1)

        # The list is page 0 of a stack rather than the central widget itself.
        # With one page a QStackedWidget lays out exactly as the widget it
        # holds. What it buys is that Setup can become a page later instead of
        # a second window, which is the whole point of the manager being the
        # main window.
        self.stack = QStackedWidget()
        self.stack.addWidget(central)
        self.list_page = central

        # What is holding the lock, and the way out if it is never going to
        # let go. Hidden whenever nothing is running, which is nearly always.
        self.lock_row = QWidget()
        lock_layout = QHBoxLayout(self.lock_row)
        lock_layout.setContentsMargins(20, 10, 20, 0)
        lock_layout.setSpacing(12)
        self.lock_banner = QLabel("")
        self.lock_banner.setObjectName("cautionText")
        self.lock_banner.setWordWrap(True)
        lock_layout.addWidget(self.lock_banner, 1)
        self.lock_release_button = QPushButton("Release")
        self.lock_release_button.setToolTip(
            "Stop waiting for this operation. It does not stop the work.")
        self.lock_release_button.clicked.connect(self._release_lock)
        lock_layout.addWidget(self.lock_release_button, 0)
        self.lock_row.hide()

        # Stack, then caution, then status: the two lower rows sit outside the
        # stack so they read the same in either state.
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.stack, 1)
        shell_layout.addWidget(self.lock_row, 0)
        shell_layout.addWidget(status_row, 0)
        self.setCentralWidget(shell)

        self._selection_changed()
        self._check_installer()

    # ── states ───────────────────────────────────────────────────────────────

    def show_list(self):
        """Back to the prefix list."""
        self.stack.setCurrentWidget(self.list_page)
        self.refresh()
        self._sync_lock_ui(force=True)

    def show_setup(self, entry):
        """Open Setup for one prefix, as a state of this window.

        The installer used to be a child process. Hosting it is what makes the
        manager the main window rather than a launcher for a second one, and it
        is also what lets the two agree about which prefix is being worked on
        and whether anything is already running."""
        # Building one takes a second or two -- it constructs the whole
        # installer window -- and it happens on this thread, so say so.
        self.status.setText(f"Opening Setup for {entry['name']}…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            page = self._setup_page(entry)
        except (aol.NotAvailable, hosted.WrongTarget) as exc:
            QMessageBox.critical(self, "Cannot open Setup", str(exc))
            return
        except Exception as exc:                  # a constructor is a lot of code
            QMessageBox.critical(
                self, "Cannot open Setup",
                f"The installer could not be built for {entry['name']}.\n\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.stack.setCurrentWidget(page)
        hosted.own_thread_exceptions(page)
        page.entered()
        self._sync_lock_ui(force=True)

    def _setup_page(self, entry):
        name = entry["name"]
        page = self._setup_pages.get(name)
        # Keyed by name, but validated against the path. Forget a prefix and
        # add a different directory under the same name -- or move one -- and
        # the cached page still drives the OLD directory, with the installer
        # already built and the WrongTarget check long past. The one guard
        # against installing into the wrong prefix is a constructor check, so a
        # page that skips the constructor skips the guard.
        if page is not None and page.path != Path(entry["path"]).expanduser():
            self.stack.removeWidget(page)
            page.dispose()
            page.deleteLater()
            del self._setup_pages[name]
            page = None
        if page is None:
            page = hosted.SetupPage(self, entry)
            self.stack.addWidget(page)
            self._setup_pages[name] = page
        self._drop_spare_setup_pages(keep=name)
        return page

    def _drop_spare_setup_pages(self, keep):
        """Hold on to the one being looked at and the one that is working.

        Each page owns a whole installer window and the threads it started, so
        they are not free to keep. They are also not safe to throw away while
        an operation is running in one -- the threads are daemon threads with
        no join -- which is why the lock holder is exempt."""
        held = self.lock.held
        busy = held.prefix if held is not None else None
        for name, page in list(self._setup_pages.items()):
            if name in (keep, busy):
                continue
            self.stack.removeWidget(page)
            page.dispose()
            page.deleteLater()
            del self._setup_pages[name]

    def current_state(self) -> str:
        return "list" if self.stack.currentWidget() is self.list_page else "setup"

    def current_prefix(self):
        """The prefix the visible page is about, or None on the list."""
        return getattr(self.stack.currentWidget(), "name", None)


    # ── busy indication ──────────────────────────────────────────────────────
    #
    # Every action that can take longer than an eyeblink says so. Qt gives an
    # indeterminate bar for free by setting both ends of the range to zero,
    # which animates rather than sitting still, so "working" and "wedged" do
    # not look the same.

    BUSY_BUTTONS = ("new_button", "find_button", "adopt_button", "launch_button",
                    "commands_button", "installer_button", "clone_button",
                    "snapshots_button", "copy_settings_button",
                    "clean_button", "protect_button", "forget_button",
                    "delete_button", "refresh_button")

    def _busy_start(self, message, maximum=0, *, prefix=None, operation=None,
                    alive=None, already_held=False):
        """Begin an operation, or refuse because another one is running.

        Two things happen before any work does. The lock is claimed, which can
        raise oplock.InUse -- callers name a prefix, so callers handle that;
        there is no sensible way to carry on regardless. And the registry
        records that the prefix is working, along with the offset its log had
        reached. If the manager dies from here on, that record is what makes
        the interruption visible at the next startup instead of leaving a
        prefix that merely looks finished.

        `alive` is the watchdog's oracle -- a QThread's isRunning for work that
        outlives this call, None for work that does not, which is read as
        still running for as long as the lock is held.

        `already_held` is for the hosted Setup page. Its claim is taken on the
        installer's own worker thread, where the answer is needed immediately
        and nothing may touch a widget; this then runs on the GUI thread to do
        the recording and the painting. Claiming twice would refuse the
        operation that had already been allowed."""
        if prefix and not already_held:
            self.lock.claim(prefix, operation or message, alive=alive)
        self._busy_prefix = prefix
        if prefix:
            label = operation or message
            try:
                offset = prefixlog.begin(prefix, label)
                entry = self.reg.by_name(prefix)
                if entry is not None:
                    entry["log"] = str(prefixlog.path_for(prefix))
                self.reg.set_working(prefix, label, log_offset=offset)
            except (KeyError, OSError) as exc:
                # Never block the work on bookkeeping -- but say so. This used
                # to be a bare pass, and it swallowed the one case that
                # mattered: set_working raises KeyError for a prefix the
                # registry does not know, which was every clone, because the
                # destination was not registered until the copy finished.
                prefixlog.write(prefix, f"Could not record the operation: {exc}")
        self.status.setText(message)
        self.progress.setRange(0, maximum)      # 0,0 == indeterminate
        self.progress.setValue(0)
        self.progress.show()
        self._sync_lock_ui()
        QApplication.processEvents()

    def _busy_step(self, value, message=None):
        if message:
            self.status.setText(message)
            if getattr(self, "_busy_prefix", None):
                prefixlog.write(self._busy_prefix, message)
        if self.progress.maximum():
            self.progress.setValue(value)
        QApplication.processEvents()

    def _busy_done(self, message="", owner=None):
        """End an operation, and give up the lock it took.

        `owner` names the prefix whose lock this is entitled to release, and
        defaults to whatever _busy_prefix says. That default is the dangerous
        one: _busy_prefix is a single piece of window state, so a late
        end_operation arriving from prefix A after the watchdog already swept
        it and prefix B took the lock would release B's claim mid-clone.
        OperationLock.release has the owner check that stops it; it was never
        being given anything to check against."""
        prefix = getattr(self, "_busy_prefix", None)
        if owner is not None and prefix is not None and owner != prefix:
            # Somebody else's operation finished late. Release only what is
            # actually theirs, and leave the current operation alone.
            self.lock.release(owner)
            return
        if prefix:
            # Released first, so that everything downstream -- _sync_lock_ui,
            # and _selection_changed under it -- sees a free lock and re-enables
            # what it disabled.
            self.lock.release(owner or prefix)
            if message:
                prefixlog.write(prefix, message)
            try:
                self.reg.clear_state(prefix)
            except KeyError:
                pass                     # forgotten or renamed while working
            self._busy_prefix = None
        self.progress.hide()
        self.progress.setRange(0, 0)
        if message:
            self.status.setText(message)
        self._sync_lock_ui()

    # ── the lock, as the window shows it ─────────────────────────────────────

    def _sync_lock_ui(self, force=False):
        """Make the window agree with the lock.

        One place decides, and it decides from the lock rather than from who
        called it, so a claim made on a Setup page disables the list's buttons
        without the two having to know about each other.

        Nothing happens unless the rendered line would change, or the caller
        says the state did. The watchdog calls this twice a second's worth of
        ticks and _selection_changed is not free -- it re-reads the protection
        flag off the selected entry -- so an idle manager should do no work
        here at all. Changing page is the case `force` exists for: the lock has
        not moved, but what should be said about it has."""
        held = self.lock.held
        here = self.current_prefix()
        line = "" if held is None else "%s on %s (%s)" % (
            held.label, held.prefix, held.elapsed)
        stamp = (line, here)
        if stamp == getattr(self, "_lock_shown", None) and not force:
            return
        self._lock_shown = stamp

        for page in self._setup_pages.values():
            page.set_actions_enabled(held is None or held.prefix == page.name)

        if held is None:
            self.lock_row.hide()
            for name in self.BUSY_BUTTONS:
                b = getattr(self, name, None)
                if b is not None:
                    b.setEnabled(True)
            self._selection_changed()
            return

        for name in self.BUSY_BUTTONS:
            b = getattr(self, name, None)
            if b is not None:
                b.setEnabled(False)

        # On the page that owns the operation there is nothing to caution
        # about: that page's own status line already says what it is doing.
        caution = self.lock.caution_for(here) if here else (
            line + " — other prefixes are unavailable until it finishes.")
        if caution is None:
            self.lock_row.hide()
            return
        self.lock_banner.setText(caution)
        self.lock_row.show()

    def _lock_tick(self):
        """The watchdog, and the clock behind the elapsed time."""
        swept = self.lock.sweep()
        if swept is not None:
            # Nothing released it, and whatever vouched for it says it has
            # stopped. Say so rather than quietly opening the lock: work that
            # ended without reporting a result is exactly the case where the
            # prefix needs looking at.
            note = "%s stopped without reporting a result." % swept.label
            if getattr(self, "_busy_prefix", None) == swept.prefix:
                self._busy_done(note)
            else:
                prefixlog.write(swept.prefix, note)
                self.status.setText("%s — %s" % (swept.prefix, note))
        self._sync_lock_ui()

    def _release_lock(self):
        """The user's way out of a lock nothing is going to release."""
        held = self.lock.held
        if held is None:
            return
        if QMessageBox.question(
            self, "Release this operation",
            f"{held.label} on {held.prefix} has been running for "
            f"{held.elapsed}.\n\n"
            "Releasing tells the manager to stop waiting for it. It does not "
            "stop the work. If that operation is in fact still running, this "
            "lets a second one start alongside it — which is the collision the "
            "lock exists to prevent.\n\n"
            f"Release {held.prefix} anyway?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Ok:
            return
        released = self.lock.release()
        if released is None:
            return                       # finished while the question was up
        prefixlog.write(released.prefix,
                        "%s released by hand after %s." % (released.label,
                                                           released.elapsed))
        if getattr(self, "_busy_prefix", None) == released.prefix:
            self._busy_done("%s released." % released.label)
        else:
            self.status.setText("%s on %s released." % (released.label,
                                                        released.prefix))
            self._sync_lock_ui()

    def _refuse(self, exc):
        """Decline a click, naming what is in the way."""
        QMessageBox.warning(
            self, "Something else is running",
            f"{exc}\n\n"
            "Only one prefix can be worked on at a time: installing into two "
            "at once collides in the package manager and in wineserver, "
            "whichever prefixes they are.\n\n"
            "Wait for it to finish, or release it from the bar at the foot of "
            "the window if you are certain it has stopped.")

    def _check_installer(self):
        """Which installer new prefixes will be built with, said out loud.

        Not a detail worth hiding: until the work is upstream, a checkout on the
        wrong branch has an installer that ignores the prefix it is handed and
        installs into ~/.AffinityLinux instead."""
        info = installer.describe()
        usable = bool(info.get("found") and info.get("supports_install_dir"))
        text = installer.summary(info)
        if not usable:
            text += f"  Settings can point at one, or fetch {installer.PREFERRED_BRANCH}."
        self.installer_label.setText(text)
        self.new_button.setEnabled(usable)

    # ── listing ──────────────────────────────────────────────────────────────

    def refresh(self):
        self._check_installer()
        self.base_label.setText(
            f"Base directory: {registry.base_dir()}   "
            f"(metadata in {registry.MANAGER_SUBDIR}/)"
        )
        self.reg.load()
        self.tree.clear()
        if not self.reg.entries:
            self.status.setText(
                "Nothing managed yet. 'New prefix' creates one under the base "
                "directory and installs into it; 'Adopt existing' takes over an "
                "install you already have, wherever it is, such as ~/.AffinityLinux."
            )
            self._selection_changed()
            return
        for entry in self.reg.entries:
            item = QTreeWidgetItem(
                [
                    entry["name"],
                    "locked" if registry.is_protected(entry) else "",
                    "…",
                    "…",
                    "…",
                    "checking…",
                    entry["path"],
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, entry["name"])
            self.tree.addTopLevelItem(item)
        for i in range(len(self.COLUMNS) - 1):
            self.tree.resizeColumnToContents(i)

        self.refresh_button.setEnabled(False)
        self.probe_thread = ProbeThread(self.reg.entries)
        self.probe_thread.done.connect(self._probed)
        self.probe_thread.start()

    def _probed(self, rows):
        self.refresh_button.setEnabled(True)
        by_name = {entry["name"]: info for entry, info in rows}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            info = by_name.get(item.data(0, Qt.ItemDataRole.UserRole))
            if not info:
                continue
            # One place decides what condition a prefix is in -- prefixstate --
            # so the badge, the tooltip, whether buttons are enabled and what
            # the recovery message says can never disagree with each other.
            # This used to be four hand-written strings that knew nothing about
            # an operation having been interrupted.
            entry = self.reg.by_name(item.data(0, Qt.ItemDataRole.UserRole)) or {}
            condition = prefixstate.classify(info["path"], entry=entry, facts=info)

            item.setText(2, info["affinity_version"] or "—")
            item.setText(3, info["wine"] or "—")
            item.setText(4, probe.human_size(info["size"]) if info["exists"] else "—")
            item.setText(5, condition.label)
            item.setData(5, Qt.ItemDataRole.UserRole, condition.name)

            tip = condition.detail
            if condition.suggestion:
                tip = f"{tip}\n\n{condition.suggestion}"
            # A prefix that could not be read reports as though it were not
            # there, which is a different problem with a different fix. Say
            # which it was.
            if info.get("unreadable"):
                tip = (f"{tip}\n\nThis prefix could not be read: "
                       f"{info['unreadable']}")
            for column in range(len(self.COLUMNS)):
                item.setToolTip(column, tip)
        for i in range(len(self.COLUMNS) - 1):
            self.tree.resizeColumnToContents(i)
        self._selection_changed()

    # ── selection ────────────────────────────────────────────────────────────

    def selected_entry(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        return self.reg.by_name(items[0].data(0, Qt.ItemDataRole.UserRole))

    def _selection_changed(self):
        # Nothing in this list is actionable while an operation holds the
        # lock. The check lives here as well as in _sync_lock_ui because
        # refresh() calls this, and a refresh during a clone would otherwise
        # hand the buttons back mid-operation.
        if self.lock.held is not None:
            for name in self.BUSY_BUTTONS:
                b = getattr(self, name, None)
                if b is not None:
                    b.setEnabled(False)
            return
        entry = self.selected_entry()
        for b in (
            self.launch_button,
            self.installer_button,
            self.forget_button,
            self.commands_button,
            self.snapshots_button,
            self.copy_settings_button,
            self.protect_button,
            self.delete_button,
            # Clone and Clean act on the selection too, and refresh() rebuilds
            # the tree without restoring it -- so these were clickable with
            # nothing selected and did nothing at all when clicked.
            self.clone_button,
            self.clean_button,
        ):
            b.setEnabled(entry is not None)
        if entry is None:
            self.protect_button.setText("Protect")
            return
        locked = registry.is_protected(entry)
        self.protect_button.setText("Unprotect" if locked else "Protect")
        # Left enabled while locked so the refusal can explain itself, rather
        # than a greyed button leaving the user guessing why.
        self.delete_button.setEnabled(True)

    # ── actions ──────────────────────────────────────────────────────────────

    def new_prefix(self):
        dialog = NewPrefixDialog(self, self.reg)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_entry:
            return
        entry = dialog.result_entry
        self.refresh()
        # Settings are NOT copied here any more. This used to offer the copy
        # before Setup, into a prefix Wine had never run in -- fine for the
        # settings, and fatal for drive letters: Wine creates C: and Z: only
        # when it creates dosdevices itself, so a W: put there first leaves a
        # prefix with no C: drive. The copy is its own action now, after Setup
        # and before Affinity is first launched.
        self.show_setup(entry)
        self.status.setText(
            f"When Setup has finished, select {entry['name']} and use "
            "'Copy settings…' before launching Affinity in it.")

    def adopt(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Existing Affinity prefix", str(Path.home())
        )
        if not chosen:
            return
        if not probe.is_prefix(chosen):
            reply = QMessageBox.question(
                self,
                "Not a Wine prefix",
                f"{chosen} has no drive_c/dosdevices, so it does not look like a "
                "Wine prefix.\n\nManage it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        suggested = Path(chosen).name.lstrip(".") or "Affinity"
        name, ok = _ask_name(self, suggested)
        if not ok:
            return
        try:
            self.reg.add(name, chosen)
        except (registry.InvalidName, registry.DuplicateName, registry.PathInUse) as e:
            QMessageBox.warning(self, "Cannot adopt this prefix", str(e))
            return
        self.refresh()

    def launch_selected(self):
        entry = self.selected_entry()
        if not entry:
            return
        pids = probe.running_pids(entry["path"])
        if pids:
            # Launching a second instance is not a nuisance here: the running
            # one dies, taking unsaved work with it.
            QMessageBox.information(
                self,
                "Already running",
                f"{entry['name']} is already running (pid {pids[0]}).\n\n"
                "Starting a second instance in the same prefix kills the first, "
                "so this was not done.",
            )
            return
        try:
            installer.launch_affinity(entry["path"])
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Cannot launch", str(e))
            return
        self.status.setText(f"Started Affinity in {entry['name']}.")

    def open_installer(self):
        entry = self.selected_entry()
        if not entry:
            return
        self.show_setup(entry)

    def show_snapshots(self):
        entry = self.selected_entry()
        if not entry:
            return
        SnapshotsDialog(self, entry).exec()

    def show_commands(self):
        """One dialog at a time, and not while the prefix is being worked on.

        It is modeless on purpose -- copying a command while looking at the
        list is the point -- which also meant a fresh one per double-click,
        each of them live, and every one of them able to run winecfg or
        wineserver -k against a prefix in the middle of a clone or an install.
        The lock covers the manager's own buttons and knew nothing about
        these."""
        entry = self.selected_entry()
        if not entry:
            return
        held = self.lock.held
        if held is not None:
            QMessageBox.information(
                self, "Something is running",
                f"{held.label} is running on {held.prefix} ({held.elapsed}).\n\n"
                "The commands here start Wine processes and end Wine sessions, "
                "which is not safe against a prefix being worked on. Wait for "
                "it to finish.")
            return
        existing = getattr(self, "_commands_dialog", None)
        if existing is not None:
            existing.close()
            existing.deleteLater()
        self._commands_dialog = CommandsDialog(self, entry)
        self._commands_dialog.show()

    def find_installations(self):
        dialog = FindDialog(self, self.reg)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def toggle_protection(self):
        entry = self.selected_entry()
        if not entry:
            return
        locked = registry.is_protected(entry)
        if locked:
            # Unprotecting gets its own confirmation. The whole value of the
            # mark is that removing it has to be deliberate; a silent toggle
            # next to a Delete button would be worth nothing.
            reply = QMessageBox.warning(
                self,
                "Remove protection",
                f"{entry['name']} is protected from deletion.\n\n"
                "Removing the protection makes it deletable. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.reg.set_protected(entry["name"], not locked)
        self.refresh()
        self.status.setText(
            f"{entry['name']} is now {'deletable' if locked else 'protected from deletion'}."
        )

    def clone_selected(self):
        """Copy the selected prefix into a new managed one.

        The copy is registered like any other prefix, so it appears in the list
        and can be launched, cleaned or deleted from here."""
        entry = self.selected_entry()
        if not entry:
            return
        name, path = entry["name"], Path(entry["path"])

        pids = probe.running_pids(path)
        if pids:
            QMessageBox.warning(
                self, "Still running",
                f"Affinity is running in {name} (pid {pids[0]}).\n\n"
                "Close it first -- a prefix copied while it is running is "
                "copied mid-write, and the registry in the copy may not match "
                "the files beside it.",
            )
            return

        suggested = self.reg.suggest_name()
        new_name, ok = _ask_name(
            self, suggested,
            title=f"Clone {name}",
            label=f"Copy {path} to a new prefix under {registry.base_dir()}.\n\nName for the copy:",
        )
        if not ok or not new_name:
            return

        try:
            new_name = registry.validate_name(new_name)
        except registry.InvalidName as exc:
            QMessageBox.warning(self, "Not a usable name", str(exc))
            return
        if self.reg.by_name(new_name):
            QMessageBox.warning(self, "Already managed",
                                f"A prefix named {new_name!r} is already in the list.")
            return

        dest = self.reg.suggest_path(new_name)
        builds = maintenance.describe_builds(path)
        keep = None
        if builds:
            chooser = BuildChoiceDialog(self, name, builds)
            if chooser.exec() != QDialog.DialogCode.Accepted:
                return
            keep = chooser.chosen()

        try:
            plan = maintenance.plan_clone(path, dest, keep_builds=keep)
        except (maintenance.NotAPrefix, maintenance.DestinationInUse) as exc:
            QMessageBox.warning(self, "Cannot clone", str(exc))
            return

        dropped = f"\nLeaving out: {', '.join(plan.drop_builds)}" if plan.drop_builds else ""
        if QMessageBox.question(
            self, "Clone this prefix",
            f"Copy {path}\n  to {dest}\n\n"
            f"About {probe.human_size(plan.est_size)}.{dropped}\n\n"
            "This reads the original and writes only to the copy.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        ) != QMessageBox.StandardButton.Ok:
            return

        # Built before the claim so the watchdog has something to ask about,
        # and claimed before start() so no work begins that the lock would have
        # refused. isRunning is bound to this thread object, not to the
        # attribute, so a later clone replacing it cannot make this operation
        # look alive.
        #
        # The lock refuses a second clone, but only once _busy_start is
        # reached -- and these three attributes are overwritten above it. A
        # second clone therefore dropped the first CloneThread's only Python
        # reference before finding out it was not allowed to run, and Qt
        # deleted a running QThread.
        running = getattr(self, "_clone_thread", None)
        if running is not None and running.isRunning():
            QMessageBox.information(
                self, "Already copying",
                f"{getattr(self, '_clone_name', 'A prefix')} is still being "
                "copied. Wait for that to finish before starting another.")
            return
        # Registered BEFORE the copy starts, not after it finishes. A clone
        # that was interrupted -- and this is the operation most likely to be,
        # since it is the long one -- left a partial multi-gigabyte tree at the
        # destination that nothing knew about: set_working raised KeyError for
        # a name the registry had never heard of, so there was no "working" row
        # for the next startup to recover, and no row to tell the user that the
        # directory sitting there is not a prefix.
        try:
            self.reg.add(new_name, dest)
        except (registry.InvalidName, registry.DuplicateName,
                registry.PathInUse) as exc:
            QMessageBox.warning(self, "Cannot clone", str(exc))
            return

        self._clone_name = new_name
        self._clone_dest = dest
        self._clone_thread = CloneThread(plan)
        try:
            self._busy_start(
                f"Copying {name} to {new_name} — {probe.human_size(plan.est_size)}",
                100, prefix=new_name, operation=f"Clone from {name}",
                alive=self._clone_thread.isRunning)
        except oplock.InUse as exc:
            self.reg.forget(new_name)    # nothing was copied; leave no row
            self._refuse(exc)
            return
        self._clone_thread.progress.connect(
            lambda pct: self._busy_step(pct, f"Copying {name} to {new_name} — {pct}%"))
        self._clone_thread.done.connect(self._clone_finished)
        self._clone_thread.start()

    def _clone_finished(self, problems, error):
        if error:
            # The row stays, and stays marked, so the partial copy at the
            # destination is visible in the list as something to deal with
            # rather than an unexplained directory.
            self._busy_done("Clone failed", owner=self._clone_name)
            try:
                self.reg.set_working(self._clone_name, "Clone (failed)")
            except KeyError:
                pass
            self.refresh()
            QMessageBox.critical(
                self, "Clone failed",
                f"{error}\n\nWhatever was copied is still at "
                f"{self._clone_dest}. {self._clone_name} is in the list, marked "
                "unfinished, so you can look at it or remove it.")
            return

        repointed = [p for p in problems if "->" in p]
        failures = [p for p in problems if "->" not in p]
        # Already in the list -- it was added before the copy began, so an
        # interruption leaves something to recover. Nothing to add here, and
        # therefore nothing that can raise in a slot.
        self._busy_done(f"Cloned to {self._clone_name}", owner=self._clone_name)
        self.refresh()

        detail = ""
        if repointed:
            detail += "\n\nSymlinks repointed into the copy:\n  " + "\n  ".join(repointed[:6])
        if failures:
            detail += "\n\nProblems:\n  " + "\n  ".join(failures[:6])
        QMessageBox.information(
            self, "Cloned",
            f"{self._clone_name} is now managed.{detail}")

    def clean_selected(self):
        """Offer what can be reclaimed from the selected prefix."""
        entry = self.selected_entry()
        if not entry:
            return
        name, path = entry["name"], Path(entry["path"])

        try:
            items = maintenance.cleanable(path)
        except maintenance.NotAPrefix as exc:
            QMessageBox.warning(self, "Not a prefix", str(exc))
            return

        if not items:
            QMessageBox.information(
                self, "Nothing to clean",
                f"{name} has no spare Wine builds, archives or saved-aside copies.")
            return

        dialog = CleanDialog(self, name, items)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.selected()
        if not chosen:
            return

        size = probe.human_size(sum(i.size for i in chosen))
        if QMessageBox.question(
            self, "Remove these?",
            f"Permanently remove {len(chosen)} item(s) from {name}, freeing about {size}.\n\n"
            "There is no undo.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Ok:
            return

        try:
            self._busy_start(f"Cleaning {name}", len(chosen),
                             prefix=name, operation="Clean")
        except oplock.InUse as exc:
            self._refuse(exc)
            return
        freed, problems = 0, []
        try:
            for n, item in enumerate(chosen, 1):
                self._busy_step(n, f"Cleaning {name} — {item.path.name}")
                got, trouble = maintenance.clean(path, [item])
                freed += got
                problems += trouble
        except maintenance.Busy as exc:
            self._busy_done("Nothing was removed")
            QMessageBox.warning(self, "Still running", str(exc))
            return

        self._busy_done(f"Freed {probe.human_size(freed)} from {name}")
        self.refresh()
        if problems:
            QMessageBox.warning(
                self, "Some items were kept",
                "Freed " + probe.human_size(freed) + ".\n\n" + "\n".join(problems[:8]))

    def delete_selected(self):
        entry = self.selected_entry()
        if not entry:
            return
        name, path = entry["name"], Path(entry["path"])

        if registry.is_protected(entry):
            QMessageBox.information(
                self,
                "Protected",
                f"{name} is protected from deletion.\n\n"
                "Use Unprotect first if you really mean to delete it.",
            )
            return

        pids = probe.running_pids(path)
        if pids:
            QMessageBox.warning(
                self,
                "Still running",
                f"Affinity is running in {name} (pid {pids[0]}).\n\n"
                "Close it first — deleting a prefix out from under a running "
                "Wine loses whatever is unsaved and leaves its services behind.",
            )
            return

        # Itemised, because the things most easily forgotten here are the ones
        # that bite later: a menu entry that launches nothing, and a document
        # association that silently stops working because the prefix holding it
        # has gone. The list is the confirmation.
        try:
            plan = removal.plan(self.reg, name)
        except KeyError:
            self.refresh()
            return
        dialog = RemovalDialog(self, plan)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        if not chosen:
            return

        # Through the lock like the others. Removing several gigabytes takes
        # long enough to overlap a clone, and a clone reading the tree this is
        # deleting from is the worst of the collisions -- it would produce a
        # copy that is quietly half a prefix.
        try:
            self._busy_start(f"Removing {name}", prefix=name, operation="Remove")
        except oplock.InUse as exc:
            self._refuse(exc)
            return
        notes = removal.apply(self.reg, plan, chosen)
        # Released before the refresh: the registry row may be gone, and
        # _busy_done clears the working state off an entry that has to exist.
        self._busy_done(f"Removed {len(chosen)} item(s) belonging to {name}")
        self.refresh()
        QMessageBox.information(self, f"{name} removed", "\n".join(notes[:14]))

    def forget_selected(self):
        entry = self.selected_entry()
        if not entry:
            return
        reply = QMessageBox.question(
            self,
            "Stop managing",
            f"Remove {entry['name']} from the list?\n\n"
            f"{entry['path']} is left exactly as it is — nothing is deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.reg.forget(entry["name"])
        self.refresh()


def _ask_name(parent, suggested, *, title="Name this prefix", label="Name"):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(460)
    ui.apply(dialog)
    field = QLineEdit(suggested)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    prompt = QLabel(label)
    prompt.setWordWrap(True)
    layout.addWidget(prompt)
    layout.addWidget(field)
    layout.addWidget(buttons)
    fit_to_contents(dialog, min_width=460)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return field.text().strip(), accepted


def main():
    app = QApplication(sys.argv)
    # The stylesheet only -- ui.apply() also sets WA_StyledBackground, which is
    # a widget attribute and not one a QApplication accepts. This is here so the
    # conflict dialogs below, which appear before any window exists, are not
    # system-default grey while the rest of the application is themed.
    app.setStyleSheet(ui.stylesheet())

    # Before the window, because the answer can be "do not open one". Two
    # managers means two writers to the prefix list and two of them driving the
    # same package manager; the standalone installer means something outside
    # this window is provisioning a prefix we think we own.
    conflict = singleinstance.check(__version__)
    if not conflict.may_start:
        QMessageBox.critical(None, "Already running", conflict.detail)
        return 1
    if conflict.action == singleinstance.WARN:
        answer = QMessageBox.warning(
            None, "Something else is running",
            conflict.detail + "\n\nStart anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return 1

    singleinstance.acquire(__version__)
    # aboutToQuit rather than a finally: Qt can leave sys.exit(app.exec())
    # by a path that does not unwind this frame.
    app.aboutToQuit.connect(singleinstance.release)

    window = ManagerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
