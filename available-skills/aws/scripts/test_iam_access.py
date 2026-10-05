"""Verify account guards, secret transport, MFA recovery and IAM scope offline."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import iam_access as iam
import aws

ACCOUNT = "123456789012"
USER = "owner@example.com"
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
SERIAL = f"arn:aws:iam::{ACCOUNT}:mfa/owner"
URI = f"otpauth://totp/AWS:owner?secret={SEED}&issuer=AWS"


class IamTests(unittest.TestCase):
    def setUp(self):
        self.config = {"expected_account_id": ACCOUNT, "region": "us-east-1", "timeout": 30}
        self.request = {"version": 1, "operation": "iam.user.create", "user_name": USER, "confirm": True}
        self.sdk = Mock()
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": ACCOUNT}
        self.credential = Mock(side_effect=lambda config, field: "ACCESS" if field == "username" else "SECRET")
        self.vault = Mock()

    def run_operation(self, request):
        with patch("boto3.Session") as session, patch.object(iam, "client", side_effect=lambda s, service, c: self.sts if service == "sts" else self.sdk):
            result = iam.execute(self.config, request, self.credential, self.vault)
            self.assertEqual(session.call_args.kwargs["aws_secret_access_key"], "SECRET")
            return result

    def test_mutations_reject_missing_confirmation_or_expected_account_before_reading_credentials(self):
        for operation in iam.WRITE_OPERATIONS:
            for confirmed, expected, code in ((False, ACCOUNT, "confirmation_required"), (True, "", "expected_account_required")):
                with self.subTest(operation=operation, confirmed=confirmed):
                    with self.assertRaises(iam.IamError) as error:
                        iam.execute({**self.config, "expected_account_id": expected},
                                    {**self.request, "operation": operation, "confirm": confirmed}, self.credential, self.vault)
                    self.assertEqual(error.exception.code, code)
        self.credential.assert_not_called()

    def test_wrong_account_prevents_user_creation(self):
        self.sts.get_caller_identity.return_value = {"Account": "000000000000"}
        with self.assertRaises(iam.IamError) as error:
            self.run_operation(self.request)
        self.assertEqual(error.exception.code, "unexpected_account")
        self.sdk.create_user.assert_not_called()

    def test_invalid_user_is_rejected_without_credentials(self):
        for user in (None, "", "owner with spaces", "a" * 65, "á"):
            with self.subTest(user=user), self.assertRaises(iam.IamError):
                iam.execute(self.config, {**self.request, "user_name": user}, self.credential, self.vault)
        self.credential.assert_not_called()

    def test_create_user_has_no_policy_side_effect(self):
        self.sdk.create_user.return_value = {"User": {"UserName": USER}}
        self.assertEqual(self.run_operation(self.request), {"user": {"UserName": USER}})
        self.sdk.create_user.assert_called_once_with(UserName=USER)
        self.sdk.attach_user_policy.assert_not_called()

    def test_console_password_goes_only_to_sdk_and_is_not_in_response(self):
        self.vault.return_value = {"value": "a-private-password"}
        self.sdk.create_login_profile.return_value = {"LoginProfile": {"UserName": USER}}
        result = self.run_operation({**self.request, "operation": "iam.user.login.create",
                                     "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner"})
        self.assertNotIn("a-private-password", json.dumps(result))
        self.sdk.create_login_profile.assert_called_once_with(UserName=USER, Password="a-private-password", PasswordResetRequired=False)

    def test_empty_password_does_not_create_login(self):
        self.vault.return_value = {"value": ""}
        with self.assertRaises(iam.IamError) as error:
            self.run_operation({**self.request, "operation": "iam.user.login.create",
                                "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner"})
        self.assertEqual(error.exception.code, "password_unavailable")
        self.sdk.create_login_profile.assert_not_called()

    def test_policy_reads_follow_pagination(self):
        self.sdk.list_attached_user_policies.side_effect = [
            {"AttachedPolicies": [{"PolicyName": "one"}], "IsTruncated": True, "Marker": "next"},
            {"AttachedPolicies": [{"PolicyName": "two"}], "IsTruncated": False},
        ]
        result = self.run_operation({**self.request, "operation": "iam.user.policy.list"})
        self.assertEqual([p["PolicyName"] for p in result["AttachedPolicies"]], ["one", "two"])

    def test_sdk_errors_never_echo_submitted_secrets(self):
        error = Exception("private secret in error")
        error.response = {"Error": {"Code": "PasswordPolicyViolation", "Message": "private secret"}}
        self.sdk.create_user.side_effect = error
        with self.assertRaises(iam.IamError) as raised:
            self.run_operation(self.request)
        self.assertNotIn("private secret", str(raised.exception))

    def test_totp_matches_rfc6238_vector(self):
        self.assertEqual(iam.totp(SEED, 59), "287082")

    def test_policy_mutations_use_only_requested_user_and_policy(self):
        self.run_operation({**self.request, "operation": "iam.user.policy.attach", "policy_arn": "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"})
        self.sdk.attach_user_policy.assert_called_once_with(UserName=USER, PolicyArn="arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess")
        document = {"Version": "2012-10-17", "Statement": []}
        self.run_operation({**self.request, "operation": "iam.user.policy.put", "policy_name": "LightsailReadOnly", "policy_document": document})
        self.assertEqual(json.loads(self.sdk.put_user_policy.call_args.kwargs["PolicyDocument"]), document)

    def test_bad_pagination_stops_instead_of_looping(self):
        self.sdk.list_mfa_devices.return_value = {"MFADevices": [], "IsTruncated": True}
        with self.assertRaises(iam.IamError) as error:
            iam.all_items(self.sdk, "list_mfa_devices", "MFADevices", UserName=USER)
        self.assertEqual(error.exception.code, "invalid_pagination")

    def test_invalid_totp_uris_are_rejected(self):
        for uri in ("not-a-uri", "otpauth://totp/user", URI + "&algorithm=SHA256", URI + "&digits=8", URI + "&period=60"):
            with self.subTest(uri=uri), self.assertRaises(iam.IamError):
                iam.seed_from_uri(uri)

    def test_existing_mfa_or_existing_totp_is_not_replaced(self):
        request = {"user_name": USER, "device_name": "owner", "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner"}
        self.sdk.list_mfa_devices.return_value = {"MFADevices": [{"SerialNumber": "another-device"}]}
        with self.assertRaises(iam.IamError) as error:
            iam.provision_mfa(self.sdk, request, ACCOUNT, self.vault)
        self.assertEqual(error.exception.code, "mfa_already_configured")
        self.sdk.list_mfa_devices.return_value = {"MFADevices": []}
        self.sdk.list_virtual_mfa_devices.return_value = {"VirtualMFADevices": []}
        self.vault.return_value = {"value": URI}
        with self.assertRaises(iam.IamError) as error:
            iam.provision_mfa(self.sdk, request, ACCOUNT, self.vault)
        self.assertEqual(error.exception.code, "totp_already_configured")
        self.sdk.create_virtual_mfa_device.assert_not_called()

    def test_vault_failure_prevents_mfa_activation(self):
        request = {"user_name": USER, "device_name": "owner", "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner"}
        self.sdk.list_mfa_devices.return_value = {"MFADevices": []}
        self.sdk.list_virtual_mfa_devices.return_value = {"VirtualMFADevices": []}
        self.sdk.create_virtual_mfa_device.return_value = {"VirtualMFADevice": {"SerialNumber": SERIAL, "Base32StringSeed": SEED.encode()}}
        def failing_vault(op, profile, path, **params):
            if "totp" in params.get("values", {}):
                raise RuntimeError("vault unavailable")
            return {"value": ""}
        with self.assertRaises(RuntimeError):
            iam.provision_mfa(self.sdk, request, ACCOUNT, failing_vault)
        self.sdk.enable_mfa_device.assert_not_called()

    def test_mfa_persists_secret_before_activation_and_never_returns_it(self):
        self.sdk.list_mfa_devices.side_effect = [{"MFADevices": []}, {"MFADevices": [{"SerialNumber": SERIAL}]}]
        self.sdk.list_virtual_mfa_devices.return_value = {"VirtualMFADevices": []}
        self.sdk.create_virtual_mfa_device.return_value = {"VirtualMFADevice": {"SerialNumber": SERIAL, "Base32StringSeed": SEED.encode()}}
        saved = {"uri": ""}
        def vault(op, profile, path, **params):
            if "totp" in params.get("values", {}): saved["uri"] = params["values"]["totp"]
            return {"value": saved["uri"]}
        def enabled(**params):
            self.assertIn(SEED, saved["uri"])
            self.assertNotEqual(params["AuthenticationCode1"], params["AuthenticationCode2"])
            return {}
        self.sdk.enable_mfa_device.side_effect = enabled
        request = {"user_name": USER, "device_name": "owner", "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner"}
        with patch.object(iam.time, "time", side_effect=[59, 61]), patch.object(iam.time, "sleep"):
            result = iam.provision_mfa(self.sdk, request, ACCOUNT, vault)
        self.assertTrue(result["enabled"])
        self.assertNotIn(SEED, json.dumps(result))

    def test_mfa_reuses_saved_unassigned_device_on_retry(self):
        self.sdk.list_mfa_devices.side_effect = [{"MFADevices": []}, {"MFADevices": [{"SerialNumber": SERIAL}]}]
        self.sdk.list_virtual_mfa_devices.return_value = {"VirtualMFADevices": [{"SerialNumber": SERIAL}]}
        self.vault.return_value = {"value": URI}
        request = {"user_name": USER, "device_name": "owner", "password_vault_profile": "vault", "password_vault_entry_path": "Passwords/owner"}
        with patch.object(iam.time, "time", side_effect=[59, 61]), patch.object(iam.time, "sleep"):
            result = iam.provision_mfa(self.sdk, request, ACCOUNT, self.vault)
        self.assertFalse(result["created"])
        self.sdk.create_virtual_mfa_device.assert_not_called()

    def test_invalid_region_rejected_before_iam_dispatch(self):
        for region in (None, "", "   ", 1):
            with patch.object(aws, "execute_iam") as dispatch, self.assertRaises(aws.SafeError):
                aws.execute(self.config, {**self.request, "region": region})
            dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
