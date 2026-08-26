from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def test_configure_bootstraps_missing_profile_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(temporary) / "configs" / "keepass.toml").is_file())


if __name__ == "__main__":
    unittest.main()
