import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not NODE, reason="node required to run wrap-url.js")
def test_hard_wrapped_url_join_and_copy():
    r = subprocess.run(
        [NODE, str(ROOT / "tests/js/wrap-url.test.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
