"""The Affinity on Linux loader the installer downloads is pinned, like the
Wine build, and installing it retires AffinityPluginLoader.

The installer's own tests cover the archive handling; this checks the pin
from the manager's side, through the same module the manager runs."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from affinity_manager import aol  # noqa: E402

try:
    inst = aol.module()
    HAVE = hasattr(inst, "AOL_LOADER_SHA256")
except aol.NotAvailable:
    inst, HAVE = None, False

pytestmark = pytest.mark.skipif(not HAVE, reason="installer without the Affinity on Linux loader")


def test_the_pin_is_a_real_checksum_for_a_versioned_release():
    assert re.fullmatch(r"[0-9a-f]{64}", inst.AOL_LOADER_SHA256)
    url = inst.aol_loader_url()
    assert url.endswith(f"/v{inst.AOL_LOADER_VERSION}/aol-loader-{inst.AOL_LOADER_VERSION}.tar.xz")


def test_affinitypluginloader_is_what_gets_retired():
    assert "AffinityHook.exe" in inst.APL_FILES
    assert inst.APL_SETTINGS["winefix.toml"] == "fixes.toml"
