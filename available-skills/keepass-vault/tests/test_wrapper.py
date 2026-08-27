from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import keepass_vault as vault


class PolicyTests(unittest.TestCase):
    def test_rejects_write_for_read_only_profile(self) -> None:
        with self.assertRaises(vault.VaultError) as raised:
            vault.check_request({"operation": "copy", "source_path": "a", "destination_path": "b", "field": "username"}, {"access": "read_only", "allowed_operations": ["copy"]})
        self.assertEqual(raised.exception.code, "write_denied")

    def test_accepts_entry_under_allowed_root(self) -> None:
        self.assertEqual(vault.check_request({"operation": "read", "path": "Work/API"}, {"allowed_operations": ["read"], "allowed_entry_roots": ["Work"]}), "read")


class TotpTests(unittest.TestCase):
    def test_list_totp_parses_one_in_memory_xml_export(self) -> None:
        backend = vault.KeePass({"database": {"windows": "vault.kdbx"}}, "irrelevant", None, 1)
        calls: list[list[str]] = []
        backend.run = lambda args, extra_input="": calls.append(args) or "<KeePassFile><Root><Group><Name>Work</Name><Entry><String><Key>Title</Key><Value>Git</Value></String><String><Key>otp</Key><Value>otpauth://hidden</Value></String></Entry><Entry><String><Key>Title</Key><Value>Mail</Value></String></Entry></Group></Root></KeePassFile>"  # type: ignore[method-assign]
        self.assertEqual(backend.list_totp(), ["Work/Git"])
        self.assertEqual(calls, [["export", "-q", "--format", "xml", "__DATABASE__"]])


if __name__ == "__main__":
    unittest.main()
