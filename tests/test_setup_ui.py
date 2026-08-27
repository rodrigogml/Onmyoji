from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "available-skills" / "setup_ui.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_setup_ui", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class KeePassProfileChooserTests(unittest.TestCase):
    def test_suggests_entry_from_integration_and_vault_profile(self) -> None:
        self.assertEqual(MODULE.suggested_vault_entry("Omie", "laveli"), "APIs/Omie:Laveli")
        self.assertEqual(MODULE.suggested_vault_entry("ForwardEmail", "cliente-principal"), "APIs/ForwardEmail:ClientePrincipal")

    def test_selects_a_numbered_keepass_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "configs" / "keepass.toml"
            config.parent.mkdir()
            config.write_text("[profiles.beta]\n[profiles.alpha]\n", encoding="utf-8")
            with patch.object(MODULE, "prompt", return_value="2"):
                self.assertEqual(MODULE.choose_keepass_profile(root), "beta")

    def test_requires_a_configured_keepass_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(MODULE, "result") as result:
            self.assertIsNone(MODULE.choose_keepass_profile(Path(temporary)))
        result.assert_called_once_with(False, "Nenhum perfil KeePass foi configurado. Configure primeiro a skill KeePass Vault.")


if __name__ == "__main__":
    unittest.main()
