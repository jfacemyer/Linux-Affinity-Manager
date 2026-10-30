"""Menu entries carry the environment the installer launches Affinity with."""
from affinity_manager import commands, defaultentry


def _prefix(tmp_path, renderer=None, build="ElementalWarrior-wine-11.20"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    reg = "WINE REGISTRY Version 2\n"
    if renderer:
        reg += f'\n[Software\\\\Wine\\\\Direct3D] 1700000000\n"renderer"="{renderer}"\n'
    (tmp_path / "user.reg").write_text(reg)
    wine = tmp_path / build / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_text("")
    (tmp_path / "ElementalWarriorWine").symlink_to(build)
    app = tmp_path / "drive_c" / "Program Files" / "Affinity" / "Affinity"
    app.mkdir(parents=True)
    (app / "AffinityHook.exe").write_text("")
    return tmp_path


def test_wayland_turns_present_wait_off(tmp_path):
    env = commands.runtime_env(_prefix(tmp_path, "vulkan"), session_type="wayland", nvidia=False)
    assert env["VKD3D_DISABLE_EXTENSIONS"] == "VK_KHR_present_id,VK_KHR_present_wait"
    assert env["WINEDLLOVERRIDES"] == "d3d12=n,b;d3d12core=n,b"
    assert env["VKD3D_SHADER_MODEL"] == "6_5"
    assert "VKD3D_CONFIG" not in env


def test_x11_leaves_present_wait_on(tmp_path):
    env = commands.runtime_env(_prefix(tmp_path, "vulkan"), session_type="x11", nvidia=True)
    assert env["VKD3D_DISABLE_EXTENSIONS"] == "VK_KHR_present_id"
    assert "VKD3D_CONFIG" not in env


def test_nvidia_on_wayland_gets_the_legacy_swapchain(tmp_path):
    env = commands.runtime_env(_prefix(tmp_path, "vulkan"), session_type="wayland", nvidia=True)
    assert env["VKD3D_CONFIG"] == "swapchain_legacy"


def test_no_renderer_setting_counts_as_vulkan(tmp_path):
    assert commands.runtime_env(_prefix(tmp_path), session_type="x11", nvidia=False)


def test_gl_and_gdi_get_nothing(tmp_path):
    assert commands.runtime_env(_prefix(tmp_path, "gl"), session_type="wayland") == {}


def test_overrides_are_joined_with_the_opencl_guard(tmp_path):
    env = commands.launch_env(_prefix(tmp_path, "vulkan"), "ElementalWarrior-wine-11.16",
                              session_type="x11", nvidia=False)
    assert env["WINEDLLOVERRIDES"] == "d3d12=n,b;d3d12core=n,b;opencl=d"
    assert env["WINEPREFIX"] == str(tmp_path)


def test_the_default_entry_carries_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    command, _ = defaultentry.launch_command(_prefix(tmp_path, "vulkan"))
    assert command.env["VKD3D_DISABLE_EXTENSIONS"] == "VK_KHR_present_id,VK_KHR_present_wait"
    assert command.env["WINEPREFIX"] == str(tmp_path)


def test_exec_quoting_follows_the_desktop_entry_spec():
    from affinity_manager.desktopentry import exec_quote
    assert exec_quote("/plain/path") == "/plain/path"
    assert exec_quote("A=b c") == '"A=b c"'
    assert exec_quote("d3d12=n,b;d3d12core=n,b") == '"d3d12=n,b;d3d12core=n,b"'
    assert exec_quote('say "hi"') == '"say \\\\"hi\\\\""'
    assert exec_quote("50%") == "50%%"


def test_the_written_entry_validates(tmp_path, monkeypatch):
    import shutil, subprocess
    import pytest
    if not shutil.which("desktop-file-validate"):
        pytest.skip("desktop-file-validate not installed")
    from affinity_manager import desktopentry
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    command, _ = defaultentry.launch_command(_prefix(tmp_path / "pfx with space", "vulkan"))
    path = desktopentry.write("Test", command)
    result = subprocess.run(["desktop-file-validate", str(path)], capture_output=True, text=True)
    assert "error" not in result.stdout + result.stderr, result.stdout + result.stderr
