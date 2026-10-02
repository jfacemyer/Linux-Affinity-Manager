"""Resize behaviour of the two side button columns (Quick Start / Troubleshooting).

Regression test for the "buttons get cut off when the window is resized" bug.

Two things caused it:

* Qt never elides button text, it clips it, and a widget's full text width is
  its layout minimum - so the panels pushed the columns wider than the window;
* the columns were pinned to ``panel.minimumSizeHint().width() + 18``, a
  minimum the layout could not honour once the window got narrow, so the panel
  overflowed its scroll area instead of scrolling.

The fix lets the labels elide (``ElidedLabel`` / ``ElidedActionButton``), gives
the columns a floor sized inside a fixed budget (``_size_side_columns``) and
asserts the resulting window minimum (``_refresh_window_minimum_width``).

Run with::

    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_side_column_layout.py -q

Offscreen only: no display, no Wine, and it never touches a live prefix.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Importing the installer module tries to pip-install PyQt6 when it is missing,
# which is not a side effect a test run should ever trigger.
pytest.importorskip(
    "PyQt6", reason="importing the installer would try to pip-install PyQt6"
)

from PyQt6.QtCore import QSize, Qt  # noqa: E402
from PyQt6.QtGui import QFontMetrics, QIcon, QPixmap  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "AffinityScripts" / "AffinityLinuxInstaller.py"
)

# Mirrors every #actionButton / #sectionTitle rule in the stylesheets: this is
# the padding that makes the labels so wide in the first place.
STYLESHEET = """
QPushButton#actionButton {
    background-color: #f5f5f7;
    color: #1d1d1f;
    border: 1px solid #e5e5e7;
    padding: 12px 24px;
    border-radius: 100px;
    font-size: 14px;
    font-weight: 500;
    text-align: left;
}
QLabel#sectionTitle { font-size: 16px; font-weight: 600; }
QLabel#statusTitle { font-size: 16px; font-weight: 600; }
"""

# The longest labels in the real side panels - these are the ones that clip.
LEFT_GROUPS = [
    (
        "Quick Start",
        [
            "One-Click Full Setup",
            "Setup Wine Environment",
            "Install System Dependencies",
            "Install Winetricks Dependencies",
        ],
    ),
    (
        "Update Affinity Applications",
        ["Affinity (Unified)", "Affinity Photo", "Affinity Designer", "Affinity Publisher"],
    ),
    (
        "Launch",
        ["Launch Affinity v3", "Launch Affinity v3 (Ubuntu Snapshot)"],
    ),
]
RIGHT_GROUPS = [
    (
        "Troubleshooting",
        [
            "Switch Wine Version",
            "Wine Configuration",
            "Winetricks",
            "Set Windows 11 + Renderer",
            "Reinstall WinMetadata",
            "Fix Canva Sign-in (v3)",
            "WebView2 Runtime (v3)",
            "Fix Settings (v3)",
        ],
    ),
    (
        "Patches",
        [
            "Return Colors (v3)",
            "Install AffinityPluginLoader",
        ],
    ),
]

# The width the user actually resizes their tiled window to.
TILED_WIDTH = 636
TILED_HEIGHT = 716
PAD_AND_BORDER = 48 + 2  # padding: 12px 24px plus a 1px border per side


@pytest.fixture(scope="session")
def ali():
    """The installer module, loaded without running the GUI."""
    spec = importlib.util.spec_from_file_location(
        "affinity_linux_installer_ui", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(app, times=8):
    for _ in range(times):
        app.processEvents()


def make_card(module, title, texts):
    card = QFrame()
    layout = QVBoxLayout(card)
    layout.setSpacing(12)
    layout.setContentsMargins(16, 16, 16, 16)

    label = module.ElidedLabel(title)
    label.setObjectName("sectionTitle")
    layout.addWidget(label)

    for text in texts:
        button = module.ElidedActionButton(text)
        button.setObjectName("actionButton")
        button.setIcon(QIcon(QPixmap(22, 22)))
        button.setIconSize(QSize(22, 22))
        button.setMinimumHeight(44)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setToolTip(text)
        layout.addWidget(button)
    return card


def make_side(module, groups):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(16)
    for title, texts in groups:
        layout.addWidget(make_card(module, title, texts))
    layout.addStretch()
    return panel


def make_status(long_message):
    """The middle column, including everything that sets its minimum width."""
    card = QFrame()
    card.setObjectName("statusCard")
    layout = QVBoxLayout(card)
    layout.setSpacing(16)
    layout.setContentsMargins(20, 20, 20, 20)

    title = QLabel("Status & Log")
    title.setObjectName("statusTitle")
    layout.addWidget(title)

    progress_label = QLabel(long_message)
    progress_label.setObjectName("progressLabel")
    progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    # production sets this - without it a long status line widens the window
    progress_label.setWordWrap(True)
    layout.addWidget(progress_label)

    progress = QProgressBar()
    progress.setFixedHeight(8)
    layout.addWidget(progress)

    row = QHBoxLayout()
    row.addStretch()
    for _ in range(3):
        button = QPushButton()
        button.setFixedSize(32, 32)
        row.addWidget(button)
    layout.addLayout(row)

    log = QTextEdit()
    log.setMinimumHeight(200)
    layout.addWidget(log)
    return card


@pytest.fixture(scope="session")
def window(ali, qapp):
    """A stand-in for the main window, built exactly like create_ui builds it.

    The two production methods under test run against a plain QMainWindow -
    neither needs anything beyond its arguments and centralWidget()/
    setMinimumWidth().
    """
    window = QMainWindow()
    window.setStyleSheet(STYLESHEET)

    central = QWidget()
    window.setCentralWidget(central)
    main_layout = QVBoxLayout(central)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(0)

    content = QWidget()
    content_layout = QHBoxLayout(content)
    content_layout.setSpacing(20)
    content_layout.setContentsMargins(20, 20, 20, 20)

    side_scrollbars = []
    status_panel = None
    for groups in (LEFT_GROUPS, None, RIGHT_GROUPS):
        if groups is None:
            status_panel = make_status(
                "Wine environment ready. Configure your distribution below."
            )
            content_layout.addWidget(status_panel, stretch=3)
            continue
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(make_side(ali, groups))
        scroll.setMaximumWidth(440)
        content_layout.addWidget(scroll, stretch=2)
        side_scrollbars.append(scroll)

    main_layout.addWidget(content, stretch=1)

    ali.AffinityInstallerGUI._size_side_columns(
        window, content_layout, status_panel, tuple(side_scrollbars)
    )
    ali.AffinityInstallerGUI._refresh_window_minimum_width(window)

    window.show()
    pump(qapp)
    window.resize(TILED_WIDTH, TILED_HEIGHT)
    pump(qapp)

    window._side_scrollbars = side_scrollbars
    window._status_panel = status_panel
    return window


def resize(window, app, width, height):
    window.resize(width, height)
    pump(app)
    return window.width(), window.height()


def elidable_widgets(scroll):
    """(widget, full_text, displayed_text, needed_width) for labels and buttons."""
    found = []
    for card in scroll.findChildren(QFrame):
        for label in card.findChildren(QLabel):
            if hasattr(label, "_full_text"):
                found.append(label)
        for button in card.findChildren(QPushButton):
            if hasattr(button, "_full_text"):
                found.append(button)
    return found


def needed_width(widget):
    """Width the widget needs to show its whole label without clipping.

    Buttons carry the QSS chrome (padding: 12px 24px + 1px border per side, plus
    the icon); section titles are `padding: 0px; border: none` in every theme, so
    they only need the text itself (ElidedLabel elides into width - 4).
    """
    text = widget.text()
    metrics = QFontMetrics(widget.font())
    if not isinstance(widget, QPushButton):
        return metrics.horizontalAdvance(text) + 4
    width = metrics.horizontalAdvance(text) + PAD_AND_BORDER
    if not widget.icon().isNull():
        width += widget.iconSize().width() + 8
    return width


def displayed_text(widget):
    """The text Qt actually paints (widget.text() returns the full label)."""
    if isinstance(widget, QPushButton):
        return QPushButton.text(widget)
    return QLabel.text(widget)


# --------------------------------------------------------------------------- #
# narrow windows
# --------------------------------------------------------------------------- #


def test_tiled_window_never_clips_its_buttons(ali, qapp, window):
    """At the user's 636px window no label may be cut mid-word."""
    assert resize(window, qapp, TILED_WIDTH, TILED_HEIGHT) == (
        TILED_WIDTH,
        TILED_HEIGHT,
    ), "the window minimum should not force this size wider"

    problems = []
    for scroll in window._side_scrollbars:
        for widget in elidable_widgets(scroll):
            shown = displayed_text(widget)
            if shown == widget.text():
                if widget.width() < needed_width(widget) - 2:
                    problems.append(f"clipped: {widget.text()!r}")
            elif not shown.endswith("…"):
                problems.append(f"cut without ellipsis: {shown!r}")
    assert not problems, problems


def test_no_horizontal_scrollbar_on_the_side_columns(ali, qapp, window):
    """A horizontal scrollbar is what made the buttons look chopped off."""
    resize(window, qapp, TILED_WIDTH, TILED_HEIGHT)
    for scroll in window._side_scrollbars:
        assert scroll.horizontalScrollBar().maximum() == 0, (
            f"{scroll} can scroll sideways, i.e. its panel is wider than the column"
        )


def test_window_survives_being_tiled_even_narrower(ali, qapp, window):
    """Ask for less than the minimum: the window clamps and nothing clips."""
    actual_width, _ = resize(window, qapp, 420, TILED_HEIGHT)
    assert actual_width == window.minimumWidth()

    for scroll in window._side_scrollbars:
        assert scroll.horizontalScrollBar().maximum() == 0
        for widget in elidable_widgets(scroll):
            shown = displayed_text(widget)
            if shown == widget.text():
                assert widget.width() >= needed_width(widget) - 2, (
                    f"clipped at the window minimum: {widget.text()!r}"
                )
            else:
                assert shown.endswith("…"), shown


def test_window_minimum_leaves_room_for_the_log_column(ali, qapp, window):
    """The budget keeps a tiled 636px window usable - no surprise growth."""
    minimum = window.minimumWidth()
    assert minimum <= TILED_WIDTH, (
        f"window minimum {minimum}px would force a {TILED_WIDTH}px window wider"
    )
    assert minimum >= 500, f"window minimum {minimum}px is unreasonably narrow"

    for scroll in window._side_scrollbars:
        assert scroll.minimumWidth() >= 150, (
            f"side column floor {scroll.minimumWidth()}px is too narrow to read"
        )


# --------------------------------------------------------------------------- #
# wide windows and scrolling
# --------------------------------------------------------------------------- #


def test_wide_window_shows_whole_labels(ali, qapp, window):
    """Eliding must not kick in when there is plenty of room."""
    resize(window, qapp, 1600, TILED_HEIGHT)

    elided = []
    for scroll in window._side_scrollbars:
        for widget in elidable_widgets(scroll):
            if displayed_text(widget) != widget.text():
                elided.append(widget.text())
    assert not elided, f"labels abbreviated despite plenty of room: {elided}"


def test_tall_content_still_scrolls_vertically(ali, qapp, window):
    """Cut off at the bottom is still cut off - the columns must scroll."""
    resize(window, qapp, TILED_WIDTH, 460)
    for scroll in window._side_scrollbars:
        assert scroll.verticalScrollBar().maximum() > 0, (
            f"{scroll} has no vertical scrollbar at 460px tall"
        )
        assert scroll.viewport().height() < scroll.widget().height(), (
            "the panel is taller than its viewport, so scrolling is required"
        )


# --------------------------------------------------------------------------- #
# callers keep seeing the real label
# --------------------------------------------------------------------------- #


def test_text_returns_the_full_label_while_elided(ali, qapp, window):
    """check_installation_status does btn.text() then setText(text + ' ✓')."""
    resize(window, qapp, 420, TILED_HEIGHT)

    button = None
    for scroll in window._side_scrollbars:
        for candidate in elidable_widgets(scroll):
            if isinstance(candidate, QPushButton) and candidate.text().startswith(
                "Affinity (Unified)"
            ):
                button = candidate
    assert button is not None, "test fixture is missing the update button"
    assert displayed_text(button) != button.text(), (
        "expected this button to be elided at 420px"
    )

    # The production pattern from check_installation_status().
    full = button.text()
    button.setText(full.split("✓")[0].strip() + " ✓")
    assert button.text() == "Affinity (Unified) ✓"
    assert button.toolTip() == "Affinity (Unified) ✓", (
        "auto-set tooltip should follow the label"
    )


def test_custom_tooltips_are_left_alone(ali, qapp, window):
    """Buttons that ship their own description keep it across a setText()."""
    resize(window, qapp, TILED_WIDTH, TILED_HEIGHT)
    button = next(
        candidate
        for scroll in window._side_scrollbars
        for candidate in elidable_widgets(scroll)
        if isinstance(candidate, QPushButton)
        and candidate.text() == "One-Click Full Setup"
    )
    button.setToolTip("Setup Wine, dependencies, and prepare for Affinity installation")
    button.setText("One-Click Full Setup (updated)")
    assert button.toolTip() == "Setup Wine, dependencies, and prepare for Affinity installation"
    assert button.text() == "One-Click Full Setup (updated)"
