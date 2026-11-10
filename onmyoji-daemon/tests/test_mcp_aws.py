import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.aws import AwsError, AwsService
from onmyoji_daemon.mcp.configuration import McpConfiguration


class Credentials:
    def resolve(self, _profile, _entry, field): return "ACCESS" if field == "username" else "SECRET"


def configured(tmp_path: Path, **changes):
    config = McpConfiguration(tmp_path)
    document = config.aws()
    profile = {"enabled": True, "description": "Conta de teste.", "credential_profile": "vault", "credential_entry_path": "APIs/AWS/test", "access_key_id_field": "username", "secret_access_key_field": "password", "cli_path": "aws", "region": "sa-east-1", "expected_account_id": "123456789012", "readable_roots": [str(tmp_path / "read")], "writable_roots": [str(tmp_path / "write")]}
    profile.update(changes); document["profiles"]["test"] = profile; config.save_aws(document)
    return AwsService(tmp_path, Credentials())


def test_identity_uses_isolated_environment_and_account_guard(tmp_path, monkeypatch):
    service = configured(tmp_path); calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs)); return Mock(returncode=0, stdout=json.dumps({"Account": "123456789012"}))
    monkeypatch.setattr("onmyoji_daemon.mcp.aws.service.subprocess.run", run)
    assert service.invoke("test", "identity_get", {})["data"]["Account"] == "123456789012"
    command, kwargs = calls[0]
    assert "SECRET" not in command
    assert kwargs["env"]["AWS_SECRET_ACCESS_KEY"] == "SECRET"
    assert kwargs["env"]["AWS_EC2_METADATA_DISABLED"] == "true"
    assert "AWS_PROFILE" not in kwargs["env"]


def test_region_and_confirmation_fail_before_credentials(tmp_path):
    credentials = Mock(); service = configured(tmp_path, region=""); service.credentials = credentials
    with pytest.raises(AwsError) as error: service.invoke("test", "identity_get", {})
    assert error.value.code == "region_required"; credentials.resolve.assert_not_called()
    with pytest.raises(AwsError) as error: service.invoke("test", "s3_object_delete", {"bucket": "b", "key": "k", "region": "us-east-1"})
    assert error.value.code == "confirmation_required"; credentials.resolve.assert_not_called()


def test_local_paths_are_confined(tmp_path, monkeypatch):
    (tmp_path / "read").mkdir(); (tmp_path / "write").mkdir(); outside = tmp_path / "outside.txt"; outside.write_text("x")
    service = configured(tmp_path)
    with pytest.raises(AwsError) as error: service.invoke("test", "s3_object_upload", {"bucket": "b", "key": "k", "source": str(outside), "confirm": True})
    assert error.value.code == "file_denied"
    monkeypatch.setattr(service, "_run", lambda _config, args: {"args": args})
    source = tmp_path / "read" / "in.txt"; source.write_text("x")
    assert service.invoke("test", "s3_object_upload", {"bucket": "b", "key": "k", "source": str(source), "confirm": True})["data"]["args"][0] == "s3api"


def test_wrong_identity_account_is_rejected(tmp_path, monkeypatch):
    service = configured(tmp_path); monkeypatch.setattr(service, "_run", lambda *_args: {"Account": "000000000000"})
    with pytest.raises(AwsError) as error: service.invoke("test", "identity_get", {})
    assert error.value.code == "unexpected_account"
