from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).parents[1] / "setupSkill.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_todoist_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def arguments(action: str, **values: str | None) -> SimpleNamespace:
    defaults = {"action": action, "profile": None, "vault_profile": None, "vault_entry": None, "vault_field": None, "access": None, "operations": None, "attachment_roots": None, "confirm_delete": None}
    defaults.update(values)
    return SimpleNamespace(**defaults)


class TodoistSetupTests(unittest.TestCase):
    def test_profile_lifecycle_uses_validated_save_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configs" / "todoist.toml"
            code, created = MODULE.profile_result(path, arguments("profile-create", profile="principal", vault_profile="local", vault_entry="APIs/Todoist:Principal"))
            self.assertEqual(code, 0)
            self.assertTrue(created["ok"])
            self.assertFalse("token" in str(created).casefold())
            code, listed = MODULE.profile_result(path, arguments("profile-list"))
            self.assertEqual(code, 0)
            self.assertEqual(listed["profiles"][0]["name"], "principal")
            code, updated = MODULE.profile_result(path, arguments("profile-update", profile="principal", access="read_write", operations="tasks.list;projects.list"))
            self.assertEqual(code, 0)
            self.assertEqual(updated["configuration"]["access"], "read_write")
            self.assertEqual(updated["configuration"]["allowed_operations"], ["tasks.list", "projects.list"])
            code, rejected = MODULE.profile_result(path, arguments("profile-delete", profile="principal"))
            self.assertEqual(code, 2)
            self.assertEqual(rejected["error"]["code"], "confirmation_required")
            code, deleted = MODULE.profile_result(path, arguments("profile-delete", profile="principal", confirm_delete="DELETE"))
            self.assertEqual(code, 0)
            self.assertTrue(deleted["ok"])

    def test_profile_create_rejects_toml_unsafe_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configs" / "todoist.toml"
            code, response = MODULE.profile_result(path, arguments("profile-create", profile="unsafe.name", vault_profile="local", vault_entry="APIs/Todoist:Unsafe"))
            self.assertEqual(code, 2)
            self.assertEqual(response["error"]["code"], "invalid_profile_name")


if __name__ == "__main__": unittest.main()
