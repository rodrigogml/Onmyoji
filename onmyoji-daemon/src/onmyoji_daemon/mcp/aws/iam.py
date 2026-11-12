"""Operações IAM restritas, com segredos mantidos no cofre MCP."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import struct
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlencode, urlparse


READ_OPERATIONS = {
    "iam_user_get", "iam_user_login_get", "iam_user_mfa_list",
    "iam_user_policy_list", "iam_user_inline_policy_list",
    "iam_user_inline_policy_get", "iam_user_groups_list",
}
WRITE_OPERATIONS = {
    "iam_user_create", "iam_user_login_create", "iam_user_mfa_provision",
    "iam_user_policy_attach", "iam_user_policy_put",
}


class IamError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message); self.code = code


def _required(request: Mapping[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip():
        raise IamError("invalid_request", f"'{name}' deve ser uma string não vazia.")
    return value.strip()


def _client(session: Any, service: str, config: Mapping[str, Any]) -> Any:
    from botocore.config import Config
    return session.client(service, config=Config(connect_timeout=config["timeout"], read_timeout=config["timeout"], retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True))


def _invoke(target: Any, method: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return getattr(target, method)(**kwargs)
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code", "request_failed")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]+", code): code = "request_failed"
        raise IamError(code, f"A solicitação AWS IAM falhou ({code}).") from None


def _all(target: Any, method: str, key: str, **params: Any) -> list[Any]:
    items: list[Any] = []
    while True:
        result = _invoke(target, method, **params); items.extend(result.get(key, []))
        if not result.get("IsTruncated"): return items
        marker = result.get("Marker")
        if not marker or marker == params.get("Marker"): raise IamError("invalid_pagination", "IAM retornou paginação inválida.")
        params["Marker"] = marker


def _totp(seed: str, timestamp: float) -> str:
    try: key = base64.b32decode(seed + "=" * ((-len(seed)) % 8), casefold=True)
    except Exception: raise IamError("invalid_totp", "A semente MFA salva é inválida.") from None
    digest = hmac.new(key, struct.pack(">Q", int(timestamp // 30)), hashlib.sha1).digest(); offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}"


def _seed(uri: str) -> str:
    parsed = urlparse(uri); query = parse_qs(parsed.query)
    if parsed.scheme != "otpauth" or parsed.netloc != "totp" or not query.get("secret"):
        raise IamError("invalid_totp", "A entrada KeePass não contém uma URI TOTP válida.")
    for key, default in (("algorithm", "SHA1"), ("digits", "6"), ("period", "30")):
        if query.get(key, [default]) != [default]: raise IamError("invalid_totp", "O MFA AWS exige SHA1, seis dígitos e período de 30 segundos.")
    return query["secret"][0]


def _provision_mfa(iam: Any, request: Mapping[str, Any], account: str, vault: Any) -> dict[str, Any]:
    user, device = _required(request, "user_name"), _required(request, "device_name")
    profile, entry = _required(request, "password_vault_profile"), _required(request, "password_vault_entry_path")
    serial = f"arn:aws:iam::{account}:mfa/{device}"
    attached = _all(iam, "list_mfa_devices", "MFADevices", UserName=user)
    if any(item["SerialNumber"] == serial for item in attached):
        _seed(vault.read_totp_uri(profile, entry)); return {"serial_number": serial, "enabled": True, "saved": True, "created": False}
    if attached: raise IamError("mfa_already_configured", "O usuário já possui outro dispositivo MFA; nada foi substituído.")
    if not vault.entry_exists(profile, entry): raise IamError("entry_not_found", "A entrada KeePass para MFA deve existir antes do provisionamento.")
    existing_uri = vault.read_totp_uri(profile, entry)
    available = _all(iam, "list_virtual_mfa_devices", "VirtualMFADevices", AssignmentStatus="Unassigned")
    orphan = next((item for item in available if item["SerialNumber"] == serial), None)
    if orphan and not existing_uri and request.get("recreate_unassigned") is True:
        _invoke(iam, "delete_virtual_mfa_device", SerialNumber=serial); orphan = None
    if orphan:
        if not existing_uri: raise IamError("mfa_seed_unavailable", "Existe dispositivo não associado sem semente salva; use recuperação explícita.")
        seed, created = _seed(existing_uri), False
    else:
        if existing_uri: raise IamError("totp_already_configured", "A entrada KeePass já possui TOTP; nada foi substituído.")
        result = _invoke(iam, "create_virtual_mfa_device", VirtualMFADeviceName=device)["VirtualMFADevice"]
        serial = result["SerialNumber"]; raw = result["Base32StringSeed"]; seed = raw.decode("ascii") if isinstance(raw, bytes) else raw
        uri = "otpauth://totp/" + quote("AWS:" + user, safe="") + "?" + urlencode({"secret": seed, "issuer": "AWS", "algorithm": "SHA1", "digits": "6", "period": "30"})
        vault.save_totp_uri(profile, entry, uri)
        if _seed(vault.read_totp_uri(profile, entry)) != seed: raise IamError("mfa_save_failed", "A semente TOTP não foi persistida; o dispositivo AWS permanece inativo.")
        created = True
    first_time = time.time(); first = _totp(seed, first_time); time.sleep(30 - first_time % 30 + 1)
    _invoke(iam, "enable_mfa_device", UserName=user, SerialNumber=serial, AuthenticationCode1=first, AuthenticationCode2=_totp(seed, time.time()))
    verified = _all(iam, "list_mfa_devices", "MFADevices", UserName=user)
    if not any(item["SerialNumber"] == serial for item in verified): raise IamError("mfa_verification_failed", "A AWS não confirmou a ativação do MFA.")
    return {"serial_number": serial, "enabled": True, "saved": True, "created": created}


def execute(config: Mapping[str, Any], operation: str, request: Mapping[str, Any], credential: Any, vault: Any) -> dict[str, Any]:
    user = _required(request, "user_name")
    if not re.fullmatch(r"[\w+=,.@-]{1,64}", user, flags=re.ASCII): raise IamError("invalid_request", "Nome de usuário IAM inválido.")
    if operation in WRITE_OPERATIONS:
        if request.get("confirm") is not True: raise IamError("confirmation_required", "Operações de escrita exigem confirm=true.")
        if not config.get("expected_account_id"): raise IamError("expected_account_required", "Mutações IAM exigem expected_account_id no perfil.")
    try: import boto3
    except ImportError: raise IamError("dependency_missing", "O runtime MCP precisa do boto3 para operações IAM.") from None
    for logger in ("boto3", "botocore", "urllib3"): logging.getLogger(logger).setLevel(logging.CRITICAL)
    session = boto3.Session(aws_access_key_id=credential(config, "access"), aws_secret_access_key=credential(config, "secret"), region_name=config["region"])
    identity = _invoke(_client(session, "sts", config), "get_caller_identity"); account = identity["Account"]
    if config.get("expected_account_id") and account != config["expected_account_id"]: raise IamError("unexpected_account", "A conta AWS resolvida não corresponde à conta esperada.")
    target = _client(session, "iam", config)
    if operation == "iam_user_get": return {"user": _invoke(target, "get_user", UserName=user)["User"]}
    if operation == "iam_user_create": return {"user": _invoke(target, "create_user", UserName=user)["User"]}
    if operation == "iam_user_login_get": return {"login_profile": _invoke(target, "get_login_profile", UserName=user)["LoginProfile"]}
    if operation == "iam_user_inline_policy_get":
        result = _invoke(target, "get_user_policy", UserName=user, PolicyName=_required(request, "policy_name"))
        return {key: result[key] for key in ("UserName", "PolicyName", "PolicyDocument")}
    if operation == "iam_user_login_create":
        profile, entry = _required(request, "password_vault_profile"), _required(request, "password_vault_entry_path")
        reset = request.get("password_reset_required", False)
        if not isinstance(reset, bool): raise IamError("invalid_request", "password_reset_required deve ser booleano.")
        try: password = vault.resolve(profile, entry, "password")
        except Exception as error: raise IamError("password_unavailable", "A entrada KeePass indicada não possui senha disponível.") from error
        response = _invoke(target, "create_login_profile", UserName=user, Password=password, PasswordResetRequired=reset); del password
        return {"login_profile": response["LoginProfile"]}
    if operation == "iam_user_mfa_provision":
        _invoke(target, "get_user", UserName=user); return _provision_mfa(target, request, account, vault)
    reads = {
        "iam_user_mfa_list": ("list_mfa_devices", "MFADevices"),
        "iam_user_policy_list": ("list_attached_user_policies", "AttachedPolicies"),
        "iam_user_inline_policy_list": ("list_user_policies", "PolicyNames"),
        "iam_user_groups_list": ("list_groups_for_user", "Groups"),
    }
    if operation in reads:
        method, key = reads[operation]; return {key: _all(target, method, key, UserName=user)}
    if operation == "iam_user_policy_attach":
        arn = _required(request, "policy_arn"); _invoke(target, "attach_user_policy", UserName=user, PolicyArn=arn); return {"attached": True, "policy_arn": arn}
    if operation == "iam_user_policy_put":
        name, document = _required(request, "policy_name"), request.get("policy_document")
        if not isinstance(document, Mapping): raise IamError("invalid_request", "policy_document deve ser um objeto JSON.")
        _invoke(target, "put_user_policy", UserName=user, PolicyName=name, PolicyDocument=json.dumps(document)); return {"saved": True, "policy_name": name}
    raise IamError("unsupported_operation", "Operação IAM não suportada.")
