"""Actual Chart.js geometry regression; no account data or browser required."""

import shutil
import subprocess
from pathlib import Path


def test_amount_bars_stay_at_zero_after_layout_animation_resize_and_legend_changes():
    result = subprocess.run(
        [shutil.which("node"), "tests/bar_baselines.cjs"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
