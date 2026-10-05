"""Constrained IAM operations with passwords and MFA seeds kept in memory.

The AWS SDK is optional for existing CLI operations. Mutations require an
expected account, verify STS before any effect, and never return secret material.
MFA is saved in KeePass before activation; failures preserve recoverable state.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import struct
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

READ_OPERATIONS = {
    "iam.user.get", "iam.user.login.get", "iam.user.mfa.list",
    "iam.user.policy.list", "iam.user.inline-policy.list", "iam.user.inline-policy.get", "iam.user.groups.list",
}
WRITE_OPERATIONS = {
    "iam.user.create", "iam.user.login.create", "iam.user.mfa.provision",
    "iam.user.policy.attach", "iam.user.policy.put",
}


class IamError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def required(request: dict[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip():
        raise IamError("invalid_request", f"'{name}' must be a non-empty string.")
    return value


def client(session: Any, service: str, config: dict[str, Any]) -> Any:
    from botocore.config import Config
    return session.client(service, config=Config(
        connect_timeout=config["timeout"], read_timeout=config["timeout"],
        retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True,
    ))


def invoke(target: Any, method: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return getattr(target, method)(**kwargs)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "request_failed")
        # AWS error messages can echo submitted parameters. Only expose the code.
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]+", code):
            code = "request_failed"
        raise IamError(code, f"AWS IAM request failed ({code}).") from None


def all_items(target: Any, method: str, key: str, **params: Any) -> list[Any]:
    items: list[Any] = []
    while True:
        result = invoke(target, method, **params)
        items.extend(result.get(key, []))
        if not result.get("IsTruncated"):
            return items
        marker = result.get("Marker")
        if not marker or marker == params.get("Marker"):
            raise IamError("invalid_pagination", "IAM returned an invalid pagination marker.")
        params["Marker"] = marker


def totp(seed: str, timestamp: float) -> str:
    try:
        key = base64.b32decode(seed + "=" * ((-len(seed)) % 8), casefold=True)
    except Exception:
        raise IamError("invalid_totp", "The saved MFA seed is invalid.") from None
    digest = hmac.new(key, struct.pack(">Q", int(timestamp // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}"


def seed_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)
    if parsed.scheme != "otpauth" or parsed.netloc != "totp" or not query.get("secret"):
        raise IamError("invalid_totp", "The KeePass entry does not contain a valid TOTP URI.")
    if any(query.get(key, [default]) != [default] for key, default in
           (("algorithm", "SHA1"), ("digits", "6"), ("period", "30"))):
        raise IamError("invalid_totp", "AWS MFA requires SHA1, six digits and a 30-second period.")
    return query["secret"][0]


def provision_mfa(iam: Any, request: dict[str, Any], account: str, vault: Any) -> dict[str, Any]:
    user = required(request, "user_name")
    device_name = required(request, "device_name")
    profile = required(request, "password_vault_profile")
    path = required(request, "password_vault_entry_path")
    expected_serial = f"arn:aws:iam::{account}:mfa/{device_name}"
    attached = all_items(iam, "list_mfa_devices", "MFADevices", UserName=user)
    if any(item["SerialNumber"] == expected_serial for item in attached):
        seed_from_uri(vault("read", profile, path, field="otp")["value"])
        return {"serial_number": expected_serial, "enabled": True, "saved": True, "created": False}
    if attached:
        raise IamError("mfa_already_configured", "The user already has another MFA device; no replacement was made.")
    # Validate the target entry and write capability before creating a device.
    vault("edit", profile, path, values={}, validate_only=True)
    existing_uri = vault("read", profile, path, field="otp")["value"]
    existing = all_items(iam, "list_virtual_mfa_devices", "VirtualMFADevices", AssignmentStatus="Unassigned")
    orphan = next((item for item in existing if item["SerialNumber"] == expected_serial), None)
    if orphan and not existing_uri and request.get("recreate_unassigned") is True:
        # Explicit recovery applies only to the requested unassigned device;
        # the earlier user-MFA check prevents removing an active authenticator.
        invoke(iam, "delete_virtual_mfa_device", SerialNumber=expected_serial)
        orphan = None
    if orphan:
        if not existing_uri:
            raise IamError("mfa_seed_unavailable", "An unassigned device exists without a saved seed; explicit recovery is required.")
        seed = seed_from_uri(existing_uri)
        created = False
    else:
        if existing_uri:
            raise IamError("totp_already_configured", "The KeePass entry already has a TOTP; no replacement was made.")
        device = invoke(iam, "create_virtual_mfa_device", VirtualMFADeviceName=device_name)["VirtualMFADevice"]
        expected_serial = device["SerialNumber"]
        raw_seed = device["Base32StringSeed"]
        seed = raw_seed.decode("ascii") if isinstance(raw_seed, bytes) else raw_seed
        uri = "otpauth://totp/" + quote("AWS:" + user, safe="") + "?" + urlencode({
            "secret": seed, "issuer": "AWS", "algorithm": "SHA1", "digits": "6", "period": "30",
        })
        vault("edit", profile, path, values={"totp": uri})
        # Verify encrypted persistence before enabling AWS MFA.
        if seed_from_uri(vault("read", profile, path, field="otp")["value"]) != seed:
            raise IamError("mfa_save_failed", "The saved TOTP did not match; the AWS device remains unassigned.")
        created = True
    first_time = time.time()
    first = totp(seed, first_time)
    time.sleep(30 - first_time % 30 + 1)
    invoke(iam, "enable_mfa_device", UserName=user, SerialNumber=expected_serial,
           AuthenticationCode1=first, AuthenticationCode2=totp(seed, time.time()))
    verified = all_items(iam, "list_mfa_devices", "MFADevices", UserName=user)
    if not any(item["SerialNumber"] == expected_serial for item in verified):
        raise IamError("mfa_verification_failed", "AWS did not confirm the enabled MFA device.")
    return {"serial_number": expected_serial, "enabled": True, "saved": True, "created": created}


def execute(config: dict[str, Any], request: dict[str, Any], credential: Any, vault: Any) -> dict[str, Any]:
    operation = request["operation"]
    user = required(request, "user_name")
    if not re.fullmatch(r"[\w+=,.@-]{1,64}", user, flags=re.ASCII):
        raise IamError("invalid_request", "The IAM user name is invalid.")
    if operation in WRITE_OPERATIONS:
        if request.get("confirm") is not True:
            raise IamError("confirmation_required", "This operation requires confirm: true.")
        if not config.get("expected_account_id"):
            raise IamError("expected_account_required", "IAM mutations require expected_account_id in the profile.")
    try:
        import boto3
    except ImportError:
        raise IamError("dependency_missing", "IAM operations require the AWS skill's boto3 dependency.") from None
    for logger in ("boto3", "botocore", "urllib3"):
        logging.getLogger(logger).setLevel(logging.CRITICAL)
    session = boto3.Session(aws_access_key_id=credential(config, "username"),
                            aws_secret_access_key=credential(config, "password"), region_name=config["region"])
    identity = invoke(client(session, "sts", config), "get_caller_identity")
    account = identity["Account"]
    if config.get("expected_account_id") and account != config["expected_account_id"]:
        raise IamError("unexpected_account", "The resolved AWS account does not match expected_account_id.")
    iam = client(session, "iam", config)
    if operation == "iam.user.get":
        return {"user": invoke(iam, "get_user", UserName=user)["User"]}
    if operation == "iam.user.create":
        return {"user": invoke(iam, "create_user", UserName=user)["User"]}
    if operation == "iam.user.login.get":
        return {"login_profile": invoke(iam, "get_login_profile", UserName=user)["LoginProfile"]}
    if operation == "iam.user.inline-policy.get":
        result = invoke(iam, "get_user_policy", UserName=user, PolicyName=required(request, "policy_name"))
        return {key: result[key] for key in ("UserName", "PolicyName", "PolicyDocument")}
    if operation == "iam.user.login.create":
        profile = required(request, "password_vault_profile")
        path = required(request, "password_vault_entry_path")
        reset = request.get("password_reset_required", False)
        if not isinstance(reset, bool):
            raise IamError("invalid_request", "password_reset_required must be a boolean.")
        password = vault("read", profile, path, field="password")["value"]
        if not isinstance(password, str) or not password:
            raise IamError("password_unavailable", "The specified KeePass entry has no password.")
        return {"login_profile": invoke(iam, "create_login_profile", UserName=user,
                                         Password=password, PasswordResetRequired=reset)["LoginProfile"]}
    if operation == "iam.user.mfa.provision":
        invoke(iam, "get_user", UserName=user)
        return provision_mfa(iam, request, account, vault)
    reads = {
        "iam.user.mfa.list": ("list_mfa_devices", "MFADevices"),
        "iam.user.policy.list": ("list_attached_user_policies", "AttachedPolicies"),
        "iam.user.inline-policy.list": ("list_user_policies", "PolicyNames"),
        "iam.user.groups.list": ("list_groups_for_user", "Groups"),
    }
    if operation in reads:
        method, key = reads[operation]
        return {key: all_items(iam, method, key, UserName=user)}
    if operation == "iam.user.policy.attach":
        arn = required(request, "policy_arn")
        invoke(iam, "attach_user_policy", UserName=user, PolicyArn=arn)
        return {"attached": True, "policy_arn": arn}
    if operation == "iam.user.policy.put":
        import json
        name = required(request, "policy_name")
        document = request.get("policy_document")
        if not isinstance(document, dict):
            raise IamError("invalid_request", "policy_document must be an object.")
        invoke(iam, "put_user_policy", UserName=user, PolicyName=name, PolicyDocument=json.dumps(document))
        return {"saved": True, "policy_name": name}
    raise IamError("unsupported_operation", "The requested IAM operation is not supported.")
