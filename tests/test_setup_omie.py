from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "omie" / "setupSkill.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_omie_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OmieSetupTests(unittest.TestCase):
    def test_profile_test_uses_a_minimal_read_only_operation(self) -> None:
        data = {"schema_version": 1, "defaults": {"timeout_seconds": 3, "max_retries": 0}, "profiles": {"laveli": {"vault_profile": "vault", "vault_entry_path": "APIs/Omie:Laveli", "app_key_field": "username", "app_secret_field": "password"}}}
        response = SimpleNamespace(returncode=0, stdout=json.dumps({"version": 1, "ok": True, "operation": "departments.list", "data": {}}))
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "choose_omie_profile", return_value="laveli"), patch.object(MODULE.subprocess, "run", return_value=response) as run, patch.object(MODULE, "result") as result:
            MODULE.test_profile(Path(temporary), Path(temporary) / "configs" / "omie.toml", data)
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request, {"version": 1, "operation": "departments.list", "params": {"page": 1, "page_size": 1}})
        self.assertIn("--profile", run.call_args.args[0])
        self.assertIn("laveli", run.call_args.args[0])
        result.assert_called_once_with(True, "Acesso à API Omie confirmado para o perfil 'laveli'.")


if __name__ == "__main__":
    unittest.main()
