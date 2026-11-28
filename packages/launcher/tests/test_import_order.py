"""Every launcher module imports first, on its own, in a fresh interpreter."""

from __future__ import annotations

import pkgutil
import subprocess
import sys

import moxi_launcher
import pytest

MODULES = sorted(m.name for m in pkgutil.iter_modules(moxi_launcher.__path__)
                 if not m.name.startswith("_"))


@pytest.mark.parametrize("name", MODULES)
def test_a_module_imports_first_on_its_own(name):
    done = subprocess.run([sys.executable, "-c", f"import moxi_launcher.{name}"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
