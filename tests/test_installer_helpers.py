"""Wine-free unit tests for AffinityLinuxInstaller.py.

Run with::

    python3 -m pytest tests/ -q

Nothing here starts Qt or Wine. The GUI class's pure helpers are bound onto a
small stand-in object, and every process-scoped test uses throw-away prefixes
under pytest's ``tmp_path`` — plus a unique prefix marker for the startup sweep
— so a live install is never touched.
"""

import importlib.util
import inspect
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

# Importing the installer module tries to pip-install PyQt6 when it is missing,
# which is not a side effect a test run should ever trigger.
pytest.importorskip(
    "PyQt6", reason="importing the installer would try to pip-install PyQt6"
)

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "AffinityScripts" / "AffinityLinuxInstaller.py"
)

# Helpers that only need `directory`, `cancel_event`, `_process_lock`,
# `_active_processes`, `log` — i.e. no Qt, no Wine.
HELPER_METHODS = (
    "build_winetricks_command",
    "_prefix_wine_pids",
    "stop_prefix_wine_processes",
    "run_command_streaming",
    "run_command",
    "get_stall_timeout",
    "_pump_child_output",
    "get_wine_dir",
    "get_wine_path",
    "_register_process",
    "_unregister_process",
    "_terminate_process",
    "has_dotnet48_runtime",
    "verify_required_wine_runtimes",
)


@pytest.fixture(scope="session")
def ali():
    """The installer module, loaded without running the GUI."""
    spec = importlib.util.spec_from_file_location(
        "affinity_linux_installer", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(ali, tmp_path):
    """A stand-in object carrying the installer's pure helpers."""

    class StandIn:
        pass

    for name in HELPER_METHODS:
        # getattr_static keeps @staticmethod wrappers. Plain getattr unwraps
        # them, so _pump_child_output became an instance method here, every
        # call passed one argument too many, the reader thread died before
        # posting its EOF sentinel, and the streaming tests hung for the full
        # stall timeout (1800s). The real class was never affected.
        setattr(StandIn, name, inspect.getattr_static(ali.AffinityInstallerGUI, name))

    obj = StandIn()
    obj.directory = str(tmp_path)
    obj.cancel_event = threading.Event()
    obj._process_lock = threading.Lock()
    obj._active_processes = set()
    obj._stalled_components = set()
    obj._log_queue = []
    obj._log_queue_lock = threading.Lock()
    obj.log_lines = []

    def log(message, level="info"):
        obj.log_lines.append((level, str(message)))

    obj.log = log
    return obj


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def spawn_with_prefix(prefix, seconds=60, executable="sleep"):
    """Start `sleep` carrying a WINEPREFIX, in its own session.

    Own session matters: `_prefix_wine_pids()` deliberately skips our own
    process group, so a child sharing pytest's group would be invisible."""
    env = {**os.environ, "WINEPREFIX": str(prefix)}
    return subprocess.Popen(
        [executable, str(seconds)], env=env, start_new_session=True
    )


def wait_dead(popen, timeout=10):
    """Wait for a child we spawned (reaps it, so /proc/pid disappears)."""
    try:
        popen.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def kill_quietly(*popens):
    for popen in popens:
        if popen is None:
            continue
        try:
            popen.kill()
        except Exception:
            pass
        try:
            popen.wait(timeout=5)
        except Exception:
            pass


def copy_as(source, target_dir, name):
    """Copy an ELF binary under a different process name."""
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / name
    shutil.copy(os.path.realpath(source), destination)
    destination.chmod(0o755)
    return destination


# --------------------------------------------------------------------------- #
# winetricks command construction
# --------------------------------------------------------------------------- #


def test_dotnet_verbs_omit_force(harness):
    """--force would re-run the whole .NET chain on every attempt."""
    for verb in ("dotnet35sp1", "dotnet48", "dotnet40", "dotnet45"):
        command = harness.build_winetricks_command(verb)
        assert "--force" not in command, command
        assert command[-1] == verb
        assert command[0] == "winetricks"
        assert "--unattended" in command


def test_other_verbs_keep_force(harness):
    assert "--force" in harness.build_winetricks_command("corefonts")
    assert "--force" in harness.build_winetricks_command("renderer=vulkan")


def test_verbose_flag_is_optional(harness):
    assert "--verbose" in harness.build_winetricks_command("dxvk")
    assert "--verbose" not in harness.build_winetricks_command("dxvk", verbose=False)


def test_component_list_is_single_source(ali):
    verbs = [verb for verb, _ in ali.WINETRICKS_COMPONENTS]
    assert verbs[:2] == ["dotnet35sp1", "dotnet48"], "dotnet must come first"
    assert len(verbs) == len(set(verbs)), "duplicate verbs would install twice"
    assert "renderer=vulkan" in verbs
    for verb, description in ali.WINETRICKS_COMPONENTS:
        assert verb and description


# --------------------------------------------------------------------------- #
# stall timeout
# --------------------------------------------------------------------------- #


def test_stall_timeout_default_and_override(harness, monkeypatch):
    monkeypatch.delenv("AFFINITY_STALL_TIMEOUT", raising=False)
    assert harness.get_stall_timeout() == 1800

    monkeypatch.setenv("AFFINITY_STALL_TIMEOUT", "42")
    assert harness.get_stall_timeout() == 42

    monkeypatch.setenv("AFFINITY_STALL_TIMEOUT", "not-a-number")
    assert harness.get_stall_timeout() == 1800

    monkeypatch.setenv("AFFINITY_STALL_TIMEOUT", "0")
    assert harness.get_stall_timeout() == 1800


# --------------------------------------------------------------------------- #
# run_command_streaming
# --------------------------------------------------------------------------- #


def test_streaming_stall_watchdog_kills_the_child(harness, tmp_path):
    """A silent child must be stopped, reported, and leave no orphan."""
    pid_file = tmp_path / "child.pid"
    command = ["bash", "-c", f"echo $$ > '{pid_file}'; exec sleep 300"]

    started = time.monotonic()
    ok = harness.run_command_streaming(command, stall_timeout=2)
    elapsed = time.monotonic() - started

    assert ok is False
    assert harness._last_command_stalled is True
    assert elapsed < 20, f"stall watchdog took {elapsed:.1f}s"

    child_pid = int(pid_file.read_text().strip())
    deadline = time.monotonic() + 5
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not Path(f"/proc/{child_pid}").exists(), "child survived the watchdog"

    assert any("stuck" in message for _, message in harness.log_lines)


def test_streaming_honours_cancel_while_silent(harness):
    harness.cancel_event.set()
    started = time.monotonic()
    ok = harness.run_command_streaming(["sleep", "300"], stall_timeout=300)
    elapsed = time.monotonic() - started

    assert ok is False
    assert elapsed < 10, f"cancel took {elapsed:.1f}s"


def test_streaming_tolerates_undecodable_output(harness):
    """Wine used to be able to kill a run with UnicodeDecodeError."""
    ok = harness.run_command_streaming(
        ["bash", "-c", "printf '\\377\\376 bad bytes\\n'; exit 0"]
    )
    assert ok is True
    assert "bad bytes" in harness._last_stream_output_text


def test_streaming_reports_failure_and_stores_output(harness):
    ok = harness.run_command_streaming(["bash", "-c", "printf 'hello\\n'; exit 3"])
    assert ok is False
    assert "hello" in harness._last_stream_output_text

    ok = harness.run_command_streaming(["bash", "-c", "printf 'hello\\n'; exit 0"])
    assert ok is True


def test_streaming_does_not_flag_normal_failure_as_stall(harness):
    harness.run_command_streaming(["bash", "-c", "exit 1"])
    assert harness._last_command_stalled is False


# --------------------------------------------------------------------------- #
# run_command
# --------------------------------------------------------------------------- #


def test_run_command_success(harness):
    ok, out, err = harness.run_command(["bash", "-c", "echo hi"], check=False)
    assert ok is True
    assert out.strip() == "hi"


def test_run_command_deadline(harness):
    """No non-streaming command may block the GUI forever."""
    started = time.monotonic()
    ok, out, err = harness.run_command(["sleep", "300"], check=False, timeout=2)
    elapsed = time.monotonic() - started

    assert ok is False
    assert "Timed out" in err
    assert elapsed < 20, f"deadline took {elapsed:.1f}s"

    remaining = subprocess.run(["pgrep", "-f", "^sleep 300$"], capture_output=True)
    assert remaining.returncode != 0, "timed-out child was left running"


def test_run_command_cancel(harness):
    harness.cancel_event.set()
    ok, out, err = harness.run_command(["sleep", "300"], check=False, timeout=300)
    assert ok is False
    assert err == "Cancelled"


def test_run_command_tolerates_undecodable_output(harness):
    ok, out, err = harness.run_command(
        ["bash", "-c", "printf '\\377\\376'; exit 0"], check=False
    )
    assert ok is True


# --------------------------------------------------------------------------- #
# prefix process scoping
# --------------------------------------------------------------------------- #


def test_prefix_pids_only_our_prefix(harness, tmp_path):
    ours = spawn_with_prefix(tmp_path)
    foreign = spawn_with_prefix(tmp_path.parent / "someone-elses-prefix")
    try:
        pids = harness._prefix_wine_pids()
        assert ours.pid in pids
        assert foreign.pid not in pids
        assert os.getpid() not in pids, "never list ourselves"
    finally:
        kill_quietly(ours, foreign)


def test_stop_prefix_wine_processes_spares_other_prefixes(harness, tmp_path):
    ours = spawn_with_prefix(tmp_path)
    foreign = spawn_with_prefix(tmp_path.parent / "someone-elses-prefix")
    try:
        assert harness.stop_prefix_wine_processes({}, reason="test") is True
        assert wait_dead(ours), "our prefix's process should be gone"
        assert foreign.poll() is None, "a foreign prefix must not be touched"
    finally:
        kill_quietly(ours, foreign)


def test_stop_prefix_wine_processes_is_a_noop_when_idle(harness):
    assert harness.stop_prefix_wine_processes({}) is True


def test_stop_prefix_wine_processes_respects_cancel_unless_forced(harness, tmp_path):
    process = spawn_with_prefix(tmp_path)
    try:
        harness.cancel_event.set()
        assert harness.stop_prefix_wine_processes({}) is False
        assert process.poll() is None, "cancelled call must not kill anything"
        harness.cancel_event.clear()
        assert (
            harness.stop_prefix_wine_processes({}, wait_seconds=5, force=True) is True
        )
        assert wait_dead(process)
    finally:
        kill_quietly(process)


def test_startup_sweep_is_scoped_to_owned_prefixes(ali, tmp_path):
    """The old `pkill -9 -f winetricks` matched unrelated command lines.

    Scoped to the exact prefix being installed into, not to a name suffix:
    `.AffinityLinux` is also a prefix of `.AffinityLinuxManager/...`, so a
    suffix or substring match reaches every managed prefix and the working
    one, and killing a wineserver ends the Affinity session it serves."""
    marker = f".AffinityLinux-test-{os.getpid()}"
    owned_prefix = tmp_path / marker
    foreign_prefix = tmp_path / "steam-compat"
    owned_prefix.mkdir()
    foreign_prefix.mkdir()

    bin_dir = tmp_path / "bin"
    fake_wineserver = copy_as("/bin/sleep", bin_dir, "wineserver")
    fake_sleep = copy_as("/bin/sleep", bin_dir, "sleep")

    target = spawn_with_prefix(owned_prefix, executable=str(fake_wineserver))
    # Right prefix, wrong name: must survive.
    decoy = spawn_with_prefix(owned_prefix, executable=str(fake_sleep))
    # Right name, wrong prefix: must survive.
    foreign = spawn_with_prefix(foreign_prefix, executable=str(fake_wineserver))
    try:
        ali.kill_stalled_wine_processes(prefix=str(owned_prefix))

        assert wait_dead(target), "stale wineserver of our prefix must be killed"
        assert decoy.poll() is None, "unrelated process in our prefix must survive"
        assert foreign.poll() is None, "another prefix's wineserver must survive"
    finally:
        kill_quietly(target, decoy, foreign)


# --------------------------------------------------------------------------- #
# .NET detection
# --------------------------------------------------------------------------- #


NET48_KEY = (
    "[Software\\\\Microsoft\\\\NET Framework Setup\\\\NDP\\\\v4\\\\Full]\n"
)


def test_has_dotnet48_runtime(harness, tmp_path):
    reg_file = tmp_path / "system.reg"
    assert harness.has_dotnet48_runtime() is False, "no system.reg yet"

    reg_file.write_text(
        NET48_KEY + '"Install"=dword:00000001\n' + '"Release"=dword:80e18\n'
    )
    assert harness.has_dotnet48_runtime() is True
    assert harness.verify_required_wine_runtimes() is True

    reg_file.write_text(NET48_KEY + '"Install"=dword:00000000\n')
    assert harness.has_dotnet48_runtime() is False, "Install=0 means not installed"


def test_has_dotnet48_runtime_ignores_other_keys(harness, tmp_path):
    reg_file = tmp_path / "system.reg"
    reg_file.write_text(
        '[Software\\\\Microsoft\\\\NET Framework Setup\\\\NDP\\\\v2.0\\\\50727]\n'
        '"Install"=dword:00000001\n'
    )
    assert harness.has_dotnet48_runtime() is False
