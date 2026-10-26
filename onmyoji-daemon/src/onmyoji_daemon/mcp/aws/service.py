"""Execução confinada de operações AWS CLI e IAM."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tomllib
from typing import Any, Mapping
from urllib.parse import quote

from ..credentials import CredentialError, KeePassCredentialResolver
from . import iam


READ_OPERATIONS = {
    "identity_get", "s3_bucket_list", "s3_bucket_location", "s3_object_list",
    "s3_object_head", "iam_role_list", "s3_batch_job_describe",
} | iam.READ_OPERATIONS
WRITE_OPERATIONS = {
    "s3_object_upload", "s3_object_download", "s3_object_download_batch",
    "s3_object_copy", "s3_object_delete", "iam_role_create",
    "iam_role_policy_put", "s3_batch_restore_create",
} | iam.WRITE_OPERATIONS
OPERATIONS = READ_OPERATIONS | WRITE_OPERATIONS


class AwsError(ValueError):
    """Erro AWS sanitizado que pode ser devolvido ao cliente MCP."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AwsError("invalid_request", f"'{key}' deve ser uma string não vazia.")
    return value.strip()


class AwsService:
    """Usa credenciais do cofre MCP sem expô-las ao agente ou ao processo filho."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "aws.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        data = self.invoke(profile_name, "identity_get", {})["data"]
        return {"profile": profile_name, "authenticated": bool(data.get("Account")), "account": data.get("Account")}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in OPERATIONS:
            raise AwsError("unsupported_operation", "Operação AWS não suportada.")
        if not isinstance(arguments, Mapping):
            raise AwsError("invalid_request", "Os argumentos devem ser um objeto JSON.")
        if operation in WRITE_OPERATIONS and arguments.get("confirm") is not True:
            raise AwsError("confirmation_required", "Operações de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        requested_region = arguments.get("region")
        if "region" in arguments and (not isinstance(requested_region, str) or not requested_region.strip()):
            raise AwsError("invalid_request", "region deve ser uma string não vazia.")
        region = requested_region.strip() if isinstance(requested_region, str) else profile.get("region", "").strip()
        if not region:
            raise AwsError("region_required", "Informe region porque o perfil não possui região padrão.")
        config = {**profile, "region": region, "timeout": defaults["timeout_seconds"], "attempts": defaults["max_attempts"]}
        if operation in iam.READ_OPERATIONS | iam.WRITE_OPERATIONS:
            try:
                data = iam.execute(config, operation, arguments, self._credential, self.credentials)
            except iam.IamError as error:
                raise AwsError(error.code, str(error)) from None
            return {"data": data}
        if operation == "s3_object_download_batch":
            return {"data": self._download_batch(config, arguments)}
        data = self._dispatch_cli(config, operation, arguments)
        return {"data": data}

    def _profile(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise AwsError("config_not_found", "Configuração AWS MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise AwsError("invalid_config", "Configuração AWS MCP inválida.") from error
        profile = document.get("profiles", {}).get(name)
        defaults = document.get("defaults", {})
        if document.get("schema_version") != 1 or not isinstance(profile, dict) or not isinstance(defaults, dict):
            raise AwsError("profile_not_found", "Perfil AWS não encontrado.")
        if profile.get("enabled") is not True:
            raise AwsError("profile_disabled", "Perfil AWS está desabilitado.")
        try:
            timeout, attempts = int(defaults["timeout_seconds"]), int(defaults["max_attempts"])
        except (KeyError, TypeError, ValueError) as error:
            raise AwsError("invalid_config", "Configuração operacional AWS inválida.") from error
        if timeout <= 0 or attempts <= 0:
            raise AwsError("invalid_config", "Configuração operacional AWS inválida.")
        account = profile.get("expected_account_id", "")
        if account and (not isinstance(account, str) or len(account) != 12 or not account.isascii() or not account.isdigit()):
            raise AwsError("invalid_config", "Conta AWS esperada inválida.")
        return profile, {"timeout_seconds": timeout, "max_attempts": attempts}

    def _credential(self, config: Mapping[str, Any], kind: str) -> str:
        field = config["access_key_id_field" if kind == "access" else "secret_access_key_field"]
        try:
            return self.credentials.resolve(config["credential_profile"], config["credential_entry_path"], field)
        except CredentialError as error:
            raise AwsError("credential_" + error.code, "Não foi possível obter a credencial AWS.") from error

    @staticmethod
    def _environment(config: Mapping[str, Any], access: str, secret: str, directory: str) -> dict[str, str]:
        environment = os.environ.copy()
        for key in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
            environment.pop(key, None)
        environment.update({
            "AWS_ACCESS_KEY_ID": access, "AWS_SECRET_ACCESS_KEY": secret,
            "AWS_REGION": config["region"], "AWS_DEFAULT_REGION": config["region"],
            "AWS_CONFIG_FILE": str(Path(directory) / "config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(Path(directory) / "credentials"),
            "AWS_EC2_METADATA_DISABLED": "true", "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off",
            "AWS_CLI_HISTORY_FILE": str(Path(directory) / "history"), "AWS_MAX_ATTEMPTS": str(config["attempts"]),
            "AWS_RETRY_MODE": "standard",
        })
        return environment

    def _run(self, config: Mapping[str, Any], arguments: list[str]) -> dict[str, Any]:
        access, secret = self._credential(config, "access"), self._credential(config, "secret")
        with tempfile.TemporaryDirectory(prefix="onmyoji-aws-") as directory:
            Path(directory, "config").write_text("", encoding="utf-8")
            Path(directory, "credentials").write_text("", encoding="utf-8")
            command = [config["cli_path"], *arguments, "--region", config["region"], "--output", "json", "--no-cli-pager", "--no-cli-auto-prompt"]
            try:
                result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=config["timeout"], env=self._environment(config, access, secret, directory), check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise AwsError("aws_cli_unavailable", "Não foi possível executar o AWS CLI.") from error
        del access, secret
        if result.returncode:
            raise AwsError("aws_cli_failed", "A solicitação AWS CLI falhou.")
        try:
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError as error:
            raise AwsError("aws_cli_failed", "O AWS CLI retornou JSON inválido.") from error

    def _dispatch_cli(self, config: Mapping[str, Any], operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "identity_get":
            data = self._run(config, ["sts", "get-caller-identity"])
            if config.get("expected_account_id") and data.get("Account") != config["expected_account_id"]:
                raise AwsError("unexpected_account", "A conta AWS resolvida não corresponde à conta esperada.")
            return data
        if operation == "s3_bucket_list": return self._run(config, ["s3api", "list-buckets"])
        if operation == "s3_bucket_location": return self._run(config, ["s3api", "get-bucket-location", "--bucket", _string(request, "bucket")])
        if operation == "iam_role_list": return self._run(config, ["iam", "list-roles"])
        if operation == "s3_batch_job_describe": return self._run(config, ["s3control", "describe-job", "--account-id", _string(request, "account_id"), "--job-id", _string(request, "job_id")])
        if operation == "iam_role_create": return self._run(config, ["iam", "create-role", "--role-name", _string(request, "role_name"), "--assume-role-policy-document", self._json_object(request, "trust_policy")])
        if operation == "iam_role_policy_put": return self._run(config, ["iam", "put-role-policy", "--role-name", _string(request, "role_name"), "--policy-name", _string(request, "policy_name"), "--policy-document", self._json_object(request, "policy_document")])
        if operation == "s3_batch_restore_create":
            return self._run(config, ["s3control", "create-job", "--account-id", _string(request, "account_id"), "--operation", self._json_object(request, "batch_operation"), "--manifest", self._json_object(request, "manifest"), "--report", self._json_object(request, "report"), "--priority", "10", "--role-arn", _string(request, "role_arn"), "--no-confirmation-required"])
        bucket = _string(request, "bucket")
        if operation == "s3_object_list":
            args = ["s3api", "list-objects-v2", "--bucket", bucket]
            for key, flag in (("prefix", "--prefix"), ("continuation_token", "--continuation-token")):
                if key in request: args += [flag, _string(request, key)]
            if "max_keys" in request:
                maximum = request["max_keys"]
                if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 1000:
                    raise AwsError("invalid_request", "max_keys deve ser um inteiro entre 1 e 1000.")
                args += ["--max-keys", str(maximum)]
            return self._run(config, args)
        key = _string(request, "key")
        if operation == "s3_object_head": return self._run(config, ["s3api", "head-object", "--bucket", bucket, "--key", key])
        if operation == "s3_object_upload":
            source = self._allowed_path(config, _string(request, "source"), "readable_roots", must_exist=True)
            if not source.is_file(): raise AwsError("source_not_found", "A origem do upload não é um arquivo.")
            return self._run(config, ["s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(source)])
        if operation == "s3_object_download":
            destination = self._allowed_path(config, _string(request, "destination"), "writable_roots")
            if destination.exists() and request.get("overwrite") is not True:
                raise AwsError("destination_exists", "O destino existe; use overwrite=true para substituí-lo.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            return self._run(config, ["s3api", "get-object", "--bucket", bucket, "--key", key, str(destination)])
        if operation == "s3_object_copy":
            source = quote(f"{_string(request, 'source_bucket')}/{_string(request, 'source_key')}", safe="/")
            return self._run(config, ["s3api", "copy-object", "--bucket", bucket, "--key", key, "--copy-source", source])
        return self._run(config, ["s3api", "delete-object", "--bucket", bucket, "--key", key])

    @staticmethod
    def _json_object(request: Mapping[str, Any], key: str) -> str:
        value = request.get(key)
        if not isinstance(value, Mapping):
            raise AwsError("invalid_request", f"'{key}' deve ser um objeto JSON.")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _allowed_path(config: Mapping[str, Any], value: str, roots_key: str, must_exist: bool = False) -> Path:
        candidate = Path(value).expanduser()
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError as error:
            raise AwsError("file_not_found", "O caminho local não existe.") from error
        for root_value in config.get(roots_key, []):
            try: root = Path(root_value).expanduser().resolve(strict=True)
            except OSError: continue
            if root.is_dir() and (resolved == root or root in resolved.parents): return resolved
        raise AwsError("file_denied", "O caminho está fora das raízes permitidas do perfil AWS.")

    def _download_batch(self, config: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
        bucket = _string(request, "bucket")
        manifest = self._allowed_path(config, _string(request, "manifest"), "readable_roots", must_exist=True)
        destination = self._allowed_path(config, _string(request, "destination"), "writable_roots")
        status_path = self._allowed_path(config, _string(request, "status_path"), "writable_roots")
        failures_path = self._allowed_path(config, _string(request, "failures_path"), "writable_roots")
        workers = request.get("workers")
        if not isinstance(workers, int) or isinstance(workers, bool) or not 1 <= workers <= 32:
            raise AwsError("invalid_request", "workers deve ser um inteiro entre 1 e 32.")
        keys = [line.split("\t", 1)[0].strip() for line in manifest.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        destination.mkdir(parents=True, exist_ok=True); status_path.parent.mkdir(parents=True, exist_ok=True); failures_path.parent.mkdir(parents=True, exist_ok=True)
        access, secret = self._credential(config, "access"), self._credential(config, "secret")
        state: dict[str, Any] = {"phase": "downloading", "total": len(keys), "completed": 0, "failed": 0, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        lock = threading.Lock()
        def save_status() -> None:
            state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            temporary = status_path.with_name(status_path.name + ".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, status_path)
        with tempfile.TemporaryDirectory(prefix="onmyoji-aws-batch-") as directory:
            Path(directory, "config").write_text("", encoding="utf-8"); Path(directory, "credentials").write_text("", encoding="utf-8")
            environment = self._environment(config, access, secret, directory)
            def download(key: str) -> tuple[str, bool]:
                relative = Path(key.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts: return key, False
                final = (destination / relative).resolve()
                if destination.resolve() not in final.parents: return key, False
                final.parent.mkdir(parents=True, exist_ok=True)
                partial = final.with_name(final.name + f".part-{threading.get_ident()}")
                command = [config["cli_path"], "s3api", "get-object", "--bucket", bucket, "--key", key, str(partial), "--region", config["region"], "--output", "json", "--no-cli-pager", "--no-cli-auto-prompt"]
                try:
                    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=config["timeout"], env=environment, check=False)
                    if not result.returncode: os.replace(partial, final); return key, True
                except (OSError, subprocess.TimeoutExpired): pass
                partial.unlink(missing_ok=True); return key, False
            failures: list[str] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                for key, ok in executor.map(download, keys):
                    with lock:
                        state["completed"] += 1
                        if not ok: state["failed"] += 1; failures.append(key)
                        if state["completed"] % 500 == 0: save_status()
        del access, secret
        failures_path.write_text("".join(key + "\n" for key in failures), encoding="utf-8")
        state["phase"] = "complete" if not failures else "complete_with_failures"; save_status()
        return state
