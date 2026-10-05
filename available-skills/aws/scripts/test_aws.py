import importlib.util
import io
import json
import os
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

    def test_profile_default_region_is_optional(self):
        for setting in ('region = ""', 'region = "   "', '', 'region = " us-east-1 "'):
            with self.subTest(setting=setting):
                profile(self.config_path)
                text = self.config_path.read_text(encoding="utf-8").replace('region = "sa-east-1"', setting)
                self.config_path.write_text(text, encoding="utf-8")
                config = aws.load_config(str(self.config_path), "test")
                self.assertEqual(config["region"], "us-east-1" if "us-east-1" in setting else "")

    def test_profile_rejects_non_string_region(self):
        for value in ('1', 'true', '[]'):
            with self.subTest(value=value):
                profile(self.config_path)
                text = self.config_path.read_text(encoding="utf-8").replace('region = "sa-east-1"', f'region = {value}')
                self.config_path.write_text(text, encoding="utf-8")
                with self.assertRaises(aws.SafeError) as context:
                    aws.load_config(str(self.config_path), "test")
                self.assertEqual(context.exception.code, "invalid_config")

    def test_request_rejects_invalid_region_instead_of_using_default(self):
        for value in (None, "", "   ", 1, True, [], {}):
            with self.subTest(region=value), patch.object(aws, "vault_field") as vault:
                with self.assertRaises(aws.SafeError) as context:
                    aws.execute(self.config, {"version": 1, "operation": "identity.get", "region": value})
                self.assertEqual(context.exception.code, "invalid_request")
                vault.assert_not_called()

    def test_missing_region_returns_json_error_before_reading_credentials(self):
        profile(self.config_path)
        text = self.config_path.read_text(encoding="utf-8").replace('region = "sa-east-1"', 'region = ""')
        self.config_path.write_text(text, encoding="utf-8")
        output = io.StringIO()
        with patch.object(aws.sys, "argv", ["aws.py", "--config", str(self.config_path), "--profile", "test"]), patch.object(aws.sys, "stdin", io.StringIO('{"version":1,"operation":"identity.get"}')), patch.object(aws.sys, "stdout", output), patch.object(aws, "vault_field") as vault:
            self.assertEqual(aws.main(), 1)
        response = json.loads(output.getvalue())
        self.assertFalse(response["ok"])
        self.assertEqual(response["operation"], "identity.get")
        self.assertEqual(response["error"]["code"], "region_required")
        self.assertIn("selected profile has no default region", response["error"]["message"])
        vault.assert_not_called()

    def test_effective_region_reaches_cli_and_environment_without_mutating_profile(self):
        for default, override, effective in (("sa-east-1", None, "sa-east-1"), ("sa-east-1", "us-east-1", "us-east-1"), ("", "eu-west-1", "eu-west-1"), ("", " us-west-2 ", "us-west-2")):
            with self.subTest(default=default, override=override):
                config = {**self.config, "region": default}
                request = {"version": 1, "operation": "s3.bucket.list"}
                if override is not None:
                    request["region"] = override
                with patch.object(aws, "vault_field", side_effect=["ACCESS", "SECRET"]), patch.object(aws.subprocess, "run", return_value=type("R", (), {"returncode": 0, "stdout": "{}"})()) as run, patch.dict(os.environ, {"AWS_REGION": "ap-south-1", "AWS_DEFAULT_REGION": "ap-south-1"}):
                    self.assertTrue(aws.execute(config, request)["ok"])
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("--region") + 1], effective)
                self.assertEqual(run.call_args.kwargs["env"]["AWS_REGION"], effective)
                self.assertEqual(run.call_args.kwargs["env"]["AWS_DEFAULT_REGION"], effective)
                self.assertEqual(config["region"], default)
                self.assertEqual(aws.load_config(str(self.config_path), "test")["region"], "sa-east-1")

    def test_batch_download_uses_effective_region(self):
        root = Path(self.temp.name)
        manifest = root / "manifest.tsv"
        manifest.write_text("one.txt\ntwo.txt\n", encoding="utf-8")
        request = {"version": 1, "operation": "s3.object.download.batch", "bucket": "b", "manifest": str(manifest), "destination": str(root / "downloads"), "status_path": str(root / "status.json"), "failures_path": str(root / "failures.txt"), "workers": 2, "confirm": True}

        def download(command, **kwargs):
            self.assertEqual(command[command.index("--region") + 1], effective)
            self.assertEqual(kwargs["env"]["AWS_REGION"], effective)
            self.assertEqual(kwargs["env"]["AWS_DEFAULT_REGION"], effective)
            Path(command[7]).write_text("downloaded", encoding="utf-8")
            return type("R", (), {"returncode": 0, "stdout": "{}"})()

        for default, override, effective in (("sa-east-1", None, "sa-east-1"), ("sa-east-1", "us-east-1", "us-east-1"), ("", "eu-west-1", "eu-west-1")):
            with self.subTest(default=default, override=override), patch.object(aws, "vault_field", side_effect=["ACCESS", "SECRET"]), patch.object(aws.subprocess, "run", side_effect=download) as run:
                config = {**self.config, "region": default}
                batch_request = {**request, **({"region": override} if override is not None else {})}
                response = aws.execute(config, batch_request)
                self.assertEqual(response["data"]["phase"], "complete")
                self.assertEqual(response["data"]["completed"], 2)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(config["region"], default)

    def test_batch_requires_region_before_creating_files(self):
        with patch.object(aws, "download_batch") as batch:
            with self.assertRaises(aws.SafeError) as context:
                aws.execute({**self.config, "region": ""}, {"version": 1, "operation": "s3.object.download.batch", "confirm": True})
        self.assertEqual(context.exception.code, "region_required")
        batch.assert_not_called()

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
