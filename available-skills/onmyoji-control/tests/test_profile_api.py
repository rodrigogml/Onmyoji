from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "setup_profile_api.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_profile_api", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ProfileApiTests(unittest.TestCase):
    def test_create_update_delete_uses_declared_schema(self) -> None:
        fields = (MODULE.Field("host", "Host", "127.0.0.1", True), MODULE.Field("vault_profile", "KeePass", required=True))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configs" / "service.toml"
            arguments = dict(path=path, load=lambda value: MODULE.simple_load(value, {"timeout": 30}), save=MODULE.simple_save, fields=fields)
            code, response = MODULE.handle(action="profile-create", profile_name="principal", values=["vault_profile=local"], confirm_delete=None, **arguments)
            self.assertEqual(code, 0)
            self.assertEqual(response["configuration"]["host"], "127.0.0.1")
            code, response = MODULE.handle(action="profile-update", profile_name="principal", values=["host=db.internal"], confirm_delete=None, **arguments)
            self.assertEqual(code, 0)
            self.assertEqual(response["configuration"]["host"], "db.internal")
            code, response = MODULE.handle(action="profile-delete", profile_name="principal", values=None, confirm_delete=None, **arguments)
            self.assertEqual(code, 2)
            self.assertEqual(response["error"]["code"], "confirmation_required")
            code, response = MODULE.handle(action="profile-delete", profile_name="principal", values=None, confirm_delete="DELETE", **arguments)
            self.assertEqual(code, 0)
            self.assertTrue(response["ok"])


if __name__ == "__main__": unittest.main()
