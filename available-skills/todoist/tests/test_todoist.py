from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "todoist.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_todoist", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TodoistTests(unittest.TestCase):
    def config(self, root: Path, access: str = "read_only") -> Path:
        path = root / "todoist.toml"
        path.write_text(f'''schema_version = 1
[defaults]
api_base = "https://api.todoist.com/api/v1"
timeout_seconds = 30
max_retries = 2
[profiles.test]
vault_profile = "vault"
vault_entry_path = "APIs/Todoist"
vault_field = "password"
access = "{access}"
allowed_operations = []
allowed_attachment_roots = []
''', encoding="utf-8")
        return path

    def test_loads_toml_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = MODULE.load_settings(self.config(Path(temporary)), "test")
            self.assertEqual(settings.vault_profile, "vault")

    def test_read_only_profile_rejects_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = MODULE.load_settings(self.config(Path(temporary)), "test")
            with self.assertRaises(MODULE.TodoistError) as error:
                MODULE.check_request({"operation": "tasks.create"}, settings)
            self.assertEqual(error.exception.code, "write_denied")

    def test_sync_uses_form_encoded_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = MODULE.load_settings(self.config(Path(temporary), "read_write"), "test")
            with patch.object(MODULE, "request_json", return_value={"sync_token": "safe"}) as request:
                result = MODULE.execute({"operation": "sync", "commands": [{"type": "item_add"}]}, settings, "secret")
            self.assertEqual(result["sync_token"], "safe")
            self.assertEqual(request.call_args.args[5], "application/x-www-form-urlencoded")
            self.assertIn(b"commands=", request.call_args.args[4])

    def test_token_is_read_from_onmyoji_keepass_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); config = self.config(root)
            settings = MODULE.load_settings(config, "test")
            payload = {"ok": True, "result": {"value": "secret-token"}}
            with patch.object(MODULE.subprocess, "run", return_value=type("Result", (), {"stdout": json.dumps(payload), "returncode": 0})()) as run:
                self.assertEqual(MODULE.read_token(settings, config), "secret-token")
            command = run.call_args.args[0]
            self.assertIn("--profile", command)
            self.assertIn("vault", command)
            self.assertNotIn("secret-token", json.dumps(command))

    def test_sanitizes_sensitive_response_fields(self) -> None:
        self.assertEqual(MODULE.sanitize({"token": "x", "value": {"password": "y"}}), {"token": "[REDACTED]", "value": {"password": "[REDACTED]"}})


if __name__ == "__main__": unittest.main()
