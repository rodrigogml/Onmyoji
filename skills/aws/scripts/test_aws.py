import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("aws_skill", Path(__file__).with_name("aws.py"))
aws = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aws)


def profile(path: Path, account="123456789012"):
    path.write_text(f'''schema_version = 1
[defaults]
timeout_seconds = 30
max_attempts = 3
[profiles.test]
region = "sa-east-1"
expected_account_id = "{account}"
vault_profile = "vault"
vault_entry_path = "AWS/example"
''', encoding="utf-8")


class AwsSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp.name) / "aws.toml"
        profile(self.config_path)
        self.config = aws.load_config(str(self.config_path), "test")

    def tearDown(self):
        self.temp.cleanup()

    def test_load_config_rejects_unknown_key(self):
        self.config_path.write_text("[aws]\nregion=x\ntimeout_seconds=1\nmax_attempts=1\nunknown=x\n[vault]\ncommand=x\nscript=x\nconfig=x\nentry_path=x\nauth_json={}\n", encoding="utf-8")
        with self.assertRaises(aws.SafeError) as context:
            aws.load_config(str(self.config_path), "test")
        self.assertEqual(context.exception.code, "invalid_config")

    def test_request_reader_accepts_powershell_utf8_bom(self):
        request = aws.read_request(io.StringIO("\ufeff{\"version\":1,\"operation\":\"identity.get\"}"))
        self.assertEqual(request["operation"], "identity.get")

    def test_request_reader_accepts_powershell_utf8_bom_bytes(self):
        request = aws.read_request(io.BytesIO(b"\xef\xbb\xbf{\"version\":1,\"operation\":\"identity.get\"}"))
        self.assertEqual(request["operation"], "identity.get")

    def test_write_requires_confirmation(self):
        with self.assertRaises(aws.SafeError) as context:
            aws.execute(self.config, {"version": 1, "operation": "s3.object.delete", "bucket": "b", "key": "k"})
        self.assertEqual(context.exception.code, "confirmation_required")

    def test_identity_rejects_wrong_account(self):
        with patch.object(aws, "run_aws", return_value={"Account": "000000000000"}):
            with self.assertRaises(aws.SafeError) as context:
                aws.execute(self.config, {"version": 1, "operation": "identity.get"})
        self.assertEqual(context.exception.code, "unexpected_account")

    @patch("subprocess.run")
    def test_cli_uses_isolated_credentials(self, run):
        run.side_effect = [
            type("R", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "result": {"value": "ACCESS"}})})(),
            type("R", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "result": {"value": "SECRET"}})})(),
            type("R", (), {"returncode": 0, "stdout": "{}"})(),
        ]
        aws.run_aws(self.config, ["sts", "get-caller-identity"])
        command, kwargs = run.call_args
        self.assertNotIn("SECRET", command)
        self.assertEqual(kwargs["env"]["AWS_ACCESS_KEY_ID"], "ACCESS")
        self.assertEqual(kwargs["env"]["AWS_SECRET_ACCESS_KEY"], "SECRET")
        self.assertEqual(kwargs["env"]["AWS_EC2_METADATA_DISABLED"], "true")
        self.assertNotIn("AWS_PROFILE", kwargs["env"])

    def test_download_does_not_overwrite_without_flag(self):
        target = Path(self.temp.name) / "target.txt"
        target.write_text("existing", encoding="utf-8")
        request = {"version": 1, "operation": "s3.object.download", "bucket": "b", "key": "k", "destination": str(target), "confirm": True}
        with self.assertRaises(aws.SafeError) as context:
            aws.execute(self.config, request)
        self.assertEqual(context.exception.code, "destination_exists")


if __name__ == "__main__":
    unittest.main()
