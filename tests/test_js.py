import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "tests/js").glob("*.test.mjs"))


@pytest.mark.skipif(not NODE, reason="node required to run the JS tests")
@pytest.mark.parametrize("script", SCRIPTS, ids=[s.name for s in SCRIPTS])
def test_js(script):
    r = subprocess.run(
        [NODE, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
