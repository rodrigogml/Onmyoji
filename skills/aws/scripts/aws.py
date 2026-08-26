#!/usr/bin/env python3
"""Run a constrained AWS CLI integration through JSON stdin/stdout."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import quote


VERSION = 1
AWS_KEYS = {"cli_path", "region", "expected_account_id", "timeout_seconds", "max_attempts"}
VAULT_KEYS = {"command", "script", "config", "entry_path", "auth_json"}
READ_OPERATIONS = {"identity.get", "s3.bucket.list", "s3.bucket.location", "s3.object.list", "s3.object.head", "iam.role.list", "s3.batch.job.describe"}
WRITE_OPERATIONS = {"s3.object.upload", "s3.object.download", "s3.object.download.batch", "s3.object.copy", "s3.object.delete", "iam.role.create", "iam.role.policy.put", "s3.batch.restore.create"}


class SafeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def error(operation: str | None, code: str, message: str) -> dict[str, Any]:
    response: dict[str, Any] = {"version": VERSION, "ok": False, "error": {"code": code, "message": message}}
    if operation:
        response["operation"] = operation
    return response


def require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SafeError("invalid_request", f"'{key}' must be a non-empty string.")
    return value


def read_request(stream: Any) -> dict[str, Any]:
    raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")
    request = json.loads(text)
    if not isinstance(request, dict):
        raise SafeError("invalid_request", "The request must be a JSON object.")
    return request


def load_config(path: str, profile_name: str) -> dict[str, Any]:
    try: data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc: raise SafeError("config_not_found", "The configuration file could not be read.") from exc
    except tomllib.TOMLDecodeError as exc: raise SafeError("invalid_config", "The configuration is invalid TOML.") from exc
    aws, vault = data.get("defaults", {}), data.get("profiles", {}).get(profile_name, {})
    if not isinstance(aws, dict) or not isinstance(vault, dict): raise SafeError("invalid_config", "The AWS profile was not found.")
    try:
        timeout = int(aws["timeout_seconds"]); attempts = int(aws["max_attempts"])
    except (ValueError, KeyError) as exc:
        raise SafeError("invalid_config", "Numeric settings or vault authentication are invalid JSON.") from exc
    if timeout <= 0 or attempts <= 0:
        raise SafeError("invalid_config", "Timeout, retries, or vault authentication are invalid.")
    account_id = aws.get("expected_account_id", vault.get("expected_account_id", ""))
    if account_id and (not account_id.isdigit() or len(account_id) != 12):
        raise SafeError("invalid_config", "expected_account_id must contain 12 digits.")
    return {
        "config_path": path,
        "cli_path": vault.get("cli_path", "aws"), "region": vault.get("region", ""),
        "expected_account_id": account_id, "timeout": timeout, "attempts": attempts,
        "vault": {"entry_path": vault.get("vault_entry_path", ""), "profile": vault.get("vault_profile", "")}, "auth": {"mode": "configured"},
    }


def vault_field(config: dict[str, Any], field: str) -> str:
    request = {"operation": "read", "path": config["vault"]["entry_path"], "field": field, "auth": config["auth"]}
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"), "--config", str(Path(config["config_path"]).parent / "keepass.toml"), "--profile", config["vault"]["profile"]]
    try:
        result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=config["timeout"], check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SafeError("vault_unavailable", "The KeePassVault provider could not be executed.") from exc
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SafeError("vault_unavailable", "The KeePassVault provider returned an invalid response.") from exc
    if result.returncode != 0 or not response.get("ok") or not isinstance(response.get("result", {}).get("value"), str):
        raise SafeError("vault_read_failed", "The AWS credential could not be read from KeePassVault.")
    return response["result"]["value"]


def aws_environment(config: dict[str, Any], access_key_id: str, secret_access_key: str, directory: str) -> dict[str, str]:
    environment = os.environ.copy()
    for key in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        environment.pop(key, None)
    environment.update({
        "AWS_ACCESS_KEY_ID": access_key_id, "AWS_SECRET_ACCESS_KEY": secret_access_key,
        "AWS_REGION": config["region"], "AWS_DEFAULT_REGION": config["region"],
        "AWS_CONFIG_FILE": str(Path(directory) / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(Path(directory) / "credentials"),
        "AWS_EC2_METADATA_DISABLED": "true", "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off",
        "AWS_CLI_HISTORY_FILE": str(Path(directory) / "history"), "AWS_MAX_ATTEMPTS": str(config["attempts"]),
        "AWS_RETRY_MODE": "standard",
    })
    return environment


def run_aws(config: dict[str, Any], arguments: list[str]) -> dict[str, Any]:
    access_key_id = vault_field(config, "username")
    secret_access_key = vault_field(config, "password")
    with tempfile.TemporaryDirectory(prefix="skillAWS-") as directory:
        Path(directory, "config").write_text("", encoding="utf-8")
        Path(directory, "credentials").write_text("", encoding="utf-8")
        command = [config["cli_path"], *arguments, "--region", config["region"], "--output", "json", "--no-cli-pager", "--no-cli-auto-prompt"]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=config["timeout"], env=aws_environment(config, access_key_id, secret_access_key, directory), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SafeError("aws_cli_unavailable", "The AWS CLI could not be executed.") from exc
    if result.returncode != 0:
        raise SafeError("aws_cli_failed", "The AWS CLI request failed.")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise SafeError("aws_cli_failed", "The AWS CLI returned an invalid JSON response.") from exc


def download_batch(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    bucket = require_string(request, "bucket")
    manifest = Path(require_string(request, "manifest"))
    destination = Path(require_string(request, "destination"))
    status_path = Path(require_string(request, "status_path"))
    failures_path = Path(require_string(request, "failures_path"))
    workers = request.get("workers")
    if not isinstance(workers, int) or not 1 <= workers <= 32:
        raise SafeError("invalid_request", "workers must be an integer between 1 and 32.")
    if not manifest.is_file():
        raise SafeError("source_not_found", "The download manifest does not exist.")
    destination.mkdir(parents=True, exist_ok=True)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(1 for line in manifest.open(encoding="utf-8") if line.strip())
    access_key_id = vault_field(config, "username")
    secret_access_key = vault_field(config, "password")
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    state = {"phase": "downloading", "total": total, "completed": 0, "failed": 0, "started_at": started_at, "updated_at": started_at}
    lock = threading.Lock()

    def save_status(force: bool = False) -> None:
        if not force and state["completed"] % 500:
            return
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        temporary = status_path.with_name(status_path.name + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, status_path)

    with tempfile.TemporaryDirectory(prefix="skillAWS-batch-") as directory:
        Path(directory, "config").write_text("", encoding="utf-8")
        Path(directory, "credentials").write_text("", encoding="utf-8")
        environment = aws_environment(config, access_key_id, secret_access_key, directory)

        def one_download(key: str) -> tuple[str, bool]:
            final_path = destination / key
            final_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path = final_path.with_name(final_path.name + f".part-{threading.get_ident()}")
            command = [config["cli_path"], "s3api", "get-object", "--bucket", bucket, "--key", key, str(partial_path), "--region", config["region"], "--output", "json", "--no-cli-pager", "--no-cli-auto-prompt"]
            try:
                result = subprocess.run(command, text=True, capture_output=True, timeout=config["timeout"], env=environment, check=False)
                if result.returncode == 0:
                    os.replace(partial_path, final_path)
                    return key, True
            except (OSError, subprocess.TimeoutExpired):
                pass
            partial_path.unlink(missing_ok=True)
            return key, False

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor, failures_path.open("w", encoding="utf-8", newline="\n") as failures:
            pending: set[concurrent.futures.Future[tuple[str, bool]]] = set()
            with manifest.open(encoding="utf-8") as source:
                for line in source:
                    key = line.split("\t", 1)[0].strip()
                    if not key:
                        continue
                    pending.add(executor.submit(one_download, key))
                    if len(pending) < workers * 4:
                        continue
                    done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in done:
                        key, ok = future.result()
                        with lock:
                            state["completed"] += 1
                            if not ok:
                                state["failed"] += 1
                                failures.write(key + "\n")
                                failures.flush()
                            save_status()
            for future in concurrent.futures.as_completed(pending):
                key, ok = future.result()
                with lock:
                    state["completed"] += 1
                    if not ok:
                        state["failed"] += 1
                        failures.write(key + "\n")
                        failures.flush()
                    save_status()
    state["phase"] = "complete" if state["failed"] == 0 else "complete_with_failures"
    save_status(force=True)
    return state


def execute(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    operation = require_string(request, "operation")
    if request.get("version") != VERSION:
        raise SafeError("unsupported_version", "Only request version 1 is supported.")
    if operation not in READ_OPERATIONS | WRITE_OPERATIONS:
        raise SafeError("unsupported_operation", "The requested operation is not supported.")
    if operation in WRITE_OPERATIONS and request.get("confirm") is not True:
        raise SafeError("confirmation_required", "This operation requires confirm: true.")
    if operation == "s3.object.download.batch":
        data = download_batch(config, request)
    if operation == "identity.get":
        data = run_aws(config, ["sts", "get-caller-identity"])
        expected = config["expected_account_id"]
        if expected and data.get("Account") != expected:
            raise SafeError("unexpected_account", "The resolved AWS account does not match expected_account_id.")
    elif operation == "s3.bucket.list":
        data = run_aws(config, ["s3api", "list-buckets"])
    elif operation == "s3.bucket.location":
        data = run_aws(config, ["s3api", "get-bucket-location", "--bucket", require_string(request, "bucket")])
    elif operation == "iam.role.list":
        data = run_aws(config, ["iam", "list-roles"])
    elif operation == "s3.batch.job.describe":
        data = run_aws(config, ["s3control", "describe-job", "--account-id", require_string(request, "account_id"), "--job-id", require_string(request, "job_id")])
    elif operation == "iam.role.create":
        data = run_aws(config, ["iam", "create-role", "--role-name", require_string(request, "role_name"), "--assume-role-policy-document", json.dumps(request["trust_policy"])])
    elif operation == "iam.role.policy.put":
        data = run_aws(config, ["iam", "put-role-policy", "--role-name", require_string(request, "role_name"), "--policy-name", require_string(request, "policy_name"), "--policy-document", json.dumps(request["policy_document"])])
    elif operation == "s3.batch.restore.create":
        data = run_aws(config, ["s3control", "create-job", "--account-id", require_string(request, "account_id"), "--operation", json.dumps(request["batch_operation"]), "--manifest", json.dumps(request["manifest"]), "--report", json.dumps(request["report"]), "--priority", "10", "--role-arn", require_string(request, "role_arn"), "--no-confirmation-required"])
    elif operation != "s3.object.download.batch":
        bucket = require_string(request, "bucket")
        if operation == "s3.object.list":
            args = ["s3api", "list-objects-v2", "--bucket", bucket]
            for request_key, argument in (("prefix", "--prefix"), ("continuation_token", "--continuation-token")):
                if request_key in request:
                    args.extend([argument, require_string(request, request_key)])
            if "max_keys" in request:
                if not isinstance(request["max_keys"], int) or not 1 <= request["max_keys"] <= 1000:
                    raise SafeError("invalid_request", "max_keys must be an integer between 1 and 1000.")
                args.extend(["--max-keys", str(request["max_keys"])])
            data = run_aws(config, args)
        elif operation == "s3.object.head":
            data = run_aws(config, ["s3api", "head-object", "--bucket", bucket, "--key", require_string(request, "key")])
        elif operation == "s3.object.upload":
            source = Path(require_string(request, "source"))
            if not source.is_file():
                raise SafeError("source_not_found", "The upload source file does not exist.")
            data = run_aws(config, ["s3api", "put-object", "--bucket", bucket, "--key", require_string(request, "key"), "--body", str(source)])
        elif operation == "s3.object.download":
            destination = Path(require_string(request, "destination"))
            if destination.exists() and request.get("overwrite") is not True:
                raise SafeError("destination_exists", "The download destination exists; set overwrite: true to replace it.")
            data = run_aws(config, ["s3api", "get-object", "--bucket", bucket, "--key", require_string(request, "key"), str(destination)])
        elif operation == "s3.object.copy":
            source_bucket = require_string(request, "source_bucket")
            source_key = require_string(request, "source_key")
            copy_source = quote(f"{source_bucket}/{source_key}", safe="/")
            data = run_aws(config, ["s3api", "copy-object", "--bucket", bucket, "--key", require_string(request, "key"), "--copy-source", copy_source])
        else:
            data = run_aws(config, ["s3api", "delete-object", "--bucket", bucket, "--key", require_string(request, "key")])
    return {"version": VERSION, "ok": True, "operation": operation, "data": data}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    operation: str | None = None
    try:
        request = read_request(sys.stdin)
        operation = request.get("operation") if isinstance(request.get("operation"), str) else None
        response = execute(load_config(args.config, args.profile), request)
    except SafeError as exc:
        response = error(operation, exc.code, exc.message)
    except json.JSONDecodeError:
        response = error(operation, "invalid_request", "The request is not valid JSON.")
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
