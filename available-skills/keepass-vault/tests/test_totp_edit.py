"""Exercise encrypted TOTP persistence and preservation of existing fields."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import keepass_vault as vault
from pykeepass import PyKeePass, create_database

URI = "otpauth://totp/AWS:owner?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=AWS"


class TotpEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.kdbx"
        kp = create_database(str(self.database), password="test-master")
        group = kp.add_group(kp.root_group, "Passwords")
        kp.add_entry(group, "owner", "user", "existing-password", notes="preserve notes")
        kp.save()
        self.backend = vault.KeePass({"database": {"windows": str(self.database), "linux": str(self.database)}}, "test-master", None, 30)

    def tearDown(self):
        self.temp.cleanup()

    def test_totp_roundtrip_preserves_password_notes_and_other_entries(self):
        self.backend.edit("Passwords/owner", {"totp": URI})
        self.assertEqual(self.backend.show("Passwords/owner", "otp"), URI)
        kp = PyKeePass(str(self.database), password="test-master")
        entry = kp.find_entries(title="owner", first=True)
        self.assertEqual(entry.password, "existing-password")
        self.assertEqual(entry.notes, "preserve notes")
        self.assertEqual(entry._get_string_field("otp"), URI)
        self.assertNotIn(URI.encode(), self.database.read_bytes())
        self.assertEqual(list(Path(self.temp.name).glob("test.kdbx.*.kdbx")), [])

    def test_validation_does_not_change_database(self):
        before = self.database.read_bytes()
        self.backend.totp_entry("Passwords/owner", validate_only=True)
        self.assertEqual(self.database.read_bytes(), before)

    def test_missing_entry_and_invalid_uri_leave_database_unchanged(self):
        before = self.database.read_bytes()
        for path, uri in (("Passwords/missing", URI), ("Passwords/owner", None), ("Passwords/owner", "not a URI")):
            if uri is None: continue
            with self.subTest(path=path, uri=uri), self.assertRaises(vault.VaultError):
                self.backend.edit(path, {"totp": uri})
            self.assertEqual(self.database.read_bytes(), before)

    def test_concurrent_change_is_not_overwritten(self):
        original_save = PyKeePass.save
        def save(kp, filename=None, **kwargs):
            original_save(kp, filename, **kwargs)
            if hasattr(filename, "write"):
                self.database.write_bytes(b"concurrent-edit")
        with patch.object(PyKeePass, "save", save), self.assertRaises(vault.VaultError) as raised:
            self.backend.edit("Passwords/owner", {"totp": URI})
        self.assertEqual(raised.exception.code, "vault_changed")
        self.assertEqual(self.database.read_bytes(), b"concurrent-edit")


if __name__ == "__main__":
    unittest.main()
