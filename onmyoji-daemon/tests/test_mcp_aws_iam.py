import json
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.aws import iam


ACCOUNT = "123456789012"
USER = "owner@example.com"
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
SERIAL = f"arn:aws:iam::{ACCOUNT}:mfa/owner"
URI = f"otpauth://totp/AWS:owner?secret={SEED}&issuer=AWS"


def context(monkeypatch):
    sdk, sts, vault = Mock(), Mock(), Mock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT}
    monkeypatch.setattr(iam, "_client", lambda _session, service, _config: sts if service == "sts" else sdk)
    monkeypatch.setattr("boto3.Session", Mock())
    config = {"expected_account_id": ACCOUNT, "region": "us-east-1", "timeout": 30}
    credential = lambda _config, kind: "ACCESS" if kind == "access" else "SECRET"
    return config, credential, vault, sdk, sts


def test_mutations_require_confirmation_and_expected_account(monkeypatch):
    config, credential, vault, sdk, _ = context(monkeypatch)
    for operation in iam.WRITE_OPERATIONS:
        with pytest.raises(iam.IamError) as error:
            iam.execute(config, operation, {"user_name": USER}, credential, vault)
        assert error.value.code == "confirmation_required"
        with pytest.raises(iam.IamError) as error:
            iam.execute({**config, "expected_account_id": ""}, operation, {"user_name": USER, "confirm": True}, credential, vault)
        assert error.value.code == "expected_account_required"
    sdk.assert_not_called()


def test_console_password_never_returns_secret(monkeypatch):
    config, credential, vault, sdk, _ = context(monkeypatch)
    vault.resolve.return_value = "private-password"
    sdk.create_login_profile.return_value = {"LoginProfile": {"UserName": USER}}
    result = iam.execute(config, "iam_user_login_create", {"user_name": USER, "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner", "confirm": True}, credential, vault)
    assert "private-password" not in json.dumps(result)
    assert sdk.create_login_profile.call_args.kwargs["Password"] == "private-password"


def test_sdk_error_is_sanitized(monkeypatch):
    config, credential, vault, sdk, _ = context(monkeypatch)
    failure = Exception("submitted private value"); failure.response = {"Error": {"Code": "PasswordPolicyViolation", "Message": "submitted private value"}}
    sdk.create_user.side_effect = failure
    with pytest.raises(iam.IamError) as error:
        iam.execute(config, "iam_user_create", {"user_name": USER, "confirm": True}, credential, vault)
    assert error.value.code == "PasswordPolicyViolation"
    assert "private value" not in str(error.value)


def test_mfa_saves_seed_before_activation(monkeypatch):
    config, credential, vault, sdk, _ = context(monkeypatch)
    sdk.get_user.return_value = {"User": {"UserName": USER}}
    sdk.list_mfa_devices.side_effect = [{"MFADevices": []}, {"MFADevices": [{"SerialNumber": SERIAL}]}]
    sdk.list_virtual_mfa_devices.return_value = {"VirtualMFADevices": []}
    sdk.create_virtual_mfa_device.return_value = {"VirtualMFADevice": {"SerialNumber": SERIAL, "Base32StringSeed": SEED.encode()}}
    vault.entry_exists.return_value = True
    saved = {"uri": ""}
    vault.read_totp_uri.side_effect = lambda *_args: saved["uri"]
    vault.save_totp_uri.side_effect = lambda _profile, _entry, uri: saved.update(uri=uri)
    def enable(**_kwargs):
        assert SEED in saved["uri"]
        return {}
    sdk.enable_mfa_device.side_effect = enable
    monkeypatch.setattr(iam.time, "time", Mock(side_effect=[59, 61])); monkeypatch.setattr(iam.time, "sleep", Mock())
    result = iam.execute(config, "iam_user_mfa_provision", {"user_name": USER, "device_name": "owner", "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner", "confirm": True}, credential, vault)
    assert result["enabled"] is True
    assert SEED not in json.dumps(result)


def test_totp_contract_and_pagination_guard():
    assert iam._totp(SEED, 59) == "287082"
    for uri in ("invalid", URI + "&algorithm=SHA256", URI + "&digits=8", URI + "&period=60"):
        with pytest.raises(iam.IamError): iam._seed(uri)
    target = Mock(); target.list_mfa_devices.return_value = {"MFADevices": [], "IsTruncated": True}
    with pytest.raises(iam.IamError) as error: iam._all(target, "list_mfa_devices", "MFADevices")
    assert error.value.code == "invalid_pagination"
