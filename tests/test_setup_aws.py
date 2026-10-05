from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "available-skills" / "aws" / "setupSkill.py"
SPEC = importlib.util.spec_from_file_location("aws_setup", SCRIPT)
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


class AwsSetupTests(unittest.TestCase):
    def test_create_interactively_without_default_region(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(SETUP, "prompt", side_effect=["1", "test", "", "", "", "", "x"]), patch.object(SETUP, "choose_keepass_profile", return_value="vault"), patch.object(SETUP, "screen"), patch.object(SETUP, "result"), patch.object(SETUP, "item"):
                SETUP.configure(root)
            self.assertEqual(SETUP.load(SETUP.path(root))["profiles"]["test"]["region"], "")

    def test_edit_region_can_clear_default_or_cancel(self):
        for value in ("", "us-east-1", "x", "\x1b"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                data = SETUP.load(SETUP.path(root))
                data["profiles"]["test"] = {"region": "sa-east-1"}
                with patch.object(SETUP, "result"):
                    SETUP.save(SETUP.path(root), data)
                with patch.object(SETUP, "prompt", side_effect=["2", "1", "2", value, "x"]), patch.object(SETUP, "screen"), patch.object(SETUP, "result"), patch.object(SETUP, "item"):
                    SETUP.configure(root)
                expected = "sa-east-1" if value in {"x", "\x1b"} else value
                self.assertEqual(SETUP.load(SETUP.path(root))["profiles"]["test"]["region"], expected)

    def test_profile_api_creates_and_clears_optional_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = SETUP.path(Path(temporary))

            def action(name, values):
                return SETUP.handle_profile(action=name, profile_name="test", values=values, confirm_delete=None, path=file, load=SETUP.load, save=SETUP.simple_save, fields=SETUP.PROFILE_FIELDS)

            code, response = action("profile-create", ["vault_profile=vault", "vault_entry_path=AWS/test"])
            self.assertEqual(code, 0, response)
            self.assertEqual(response["configuration"]["region"], "")
            code, response = action("profile-update", ["region=us-east-1"])
            self.assertEqual(code, 0, response)
            self.assertEqual(SETUP.load(file)["profiles"]["test"]["region"], "us-east-1")
            code, response = action("profile-update", ["region="])
            self.assertEqual(code, 0, response)
            self.assertEqual(SETUP.load(file)["profiles"]["test"]["region"], "")


if __name__ == "__main__":
    unittest.main()
