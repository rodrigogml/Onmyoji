#!/usr/bin/env python3
"""Safe JSON wrapper for a local EccoVox speech runtime."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import tomllib

VERSION = 1
ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".webm", ".flac"}
ALLOWED_TTS_FORMATS = {"mp3", "wav", "opus", "flac"}
CONTENT_TYPES = {"mp3": {"audio/mpeg", "audio/mp3"}, "wav": {"audio/wav", "audio/x-wav"}, "opus": {"audio/ogg", "audio/opus"}, "flac": {"audio/flac"}}


class SafeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"version": VERSION, "ok": False, "error": {"code": code, "message": message}}


def _success(operation: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"version": VERSION, "ok": True, "operation": operation, "data": data}


def _positive(value: Any, name: str, maximum: int) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise SafeError("invalid_config", f"{name} must be an integer.") from exc
    if not 1 <= result <= maximum:
        raise SafeError("invalid_config", f"{name} must be between 1 and {maximum}.")
    return result


def _parse_roots(value: Any, field: str) -> tuple[Path, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SafeError("invalid_config", f"{field} must be an array of directory paths.")
    roots = tuple(Path(item).expanduser().resolve() for item in value)
    for root in roots:
        if not root.is_absolute() or not root.is_dir():
            raise SafeError("invalid_config", f"{field} contains an unavailable directory.")
    return roots


def load_config(path: str, profile_name: str) -> dict[str, Any]:
    try:
        document = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SafeError("invalid_config", "Configuration file could not be read.") from exc
    try:
        defaults = document.get("defaults", {})
        profiles = document["profiles"]
        profile = profiles[profile_name]
        if not isinstance(defaults, dict) or not isinstance(profile, dict):
            raise KeyError(profile_name)
        values = {**defaults, **profile}
        base_url = str(values["base_url"]).strip()
        timeout = _positive(values.get("request_timeout_seconds", 120), "request_timeout_seconds", 300)
        max_audio = _positive(values.get("max_audio_bytes", 10485760), "max_audio_bytes", 104857600)
        max_text = _positive(values.get("max_text_characters", 4000), "max_text_characters", 100000)
        readable = _parse_roots(values.get("readable_roots", []), "readable_roots")
        writable = _parse_roots(values.get("writable_roots", []), "writable_roots")
    except (KeyError, TypeError) as exc:
        raise SafeError("invalid_config", "Missing or invalid profile configuration.") from exc
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise SafeError("invalid_config", "server.base_url must be an explicit loopback HTTP URL without extra components.")
    return {"base_url": base_url.rstrip("/"), "timeout": timeout, "max_audio": max_audio, "max_text": max_text, "readable": readable, "writable": writable}


def _contained(path_value: Any, roots: tuple[Path, ...], field: str, must_exist: bool) -> Path:
    if not isinstance(path_value, str) or not path_value.strip():
        raise SafeError("invalid_request", f"{field} is required.")
    candidate = Path(path_value).resolve()
    if must_exist and not candidate.is_file():
        raise SafeError("invalid_request", f"{field} must identify an existing file.")
    if not roots or not any(candidate.is_relative_to(root) for root in roots):
        raise SafeError("path_not_allowed", f"{field} is outside the configured allowed roots.")
    return candidate


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise SafeError("runtime_error", "EccoVox returned an unexpected redirect.")


def _request(config: dict[str, Any], endpoint: str, body: bytes | None, content_type: str | None) -> tuple[bytes, str]:
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(config["base_url"] + endpoint, data=body, headers=headers, method="POST" if body is not None else "GET")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=config["timeout"]) as response:
            return response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        raise SafeError("runtime_error", f"EccoVox rejected the request ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise SafeError("runtime_unavailable", "EccoVox is unavailable.") from exc
    except TimeoutError as exc:
        raise SafeError("timeout", "EccoVox request timed out.") from exc


def _json_response(config: dict[str, Any], endpoint: str, body: bytes | None, content_type: str | None) -> dict[str, Any]:
    raw, response_type = _request(config, endpoint, body, content_type)
    if response_type != "application/json":
        raise SafeError("runtime_error", "EccoVox returned an unexpected response.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafeError("runtime_error", "EccoVox returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise SafeError("runtime_error", "EccoVox returned an invalid response.")
    return payload


def _multipart(audio: Path, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----EccoVox" + secrets.token_hex(16)
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"))
    mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
    chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), audio.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def health(config: dict[str, Any]) -> dict[str, Any]:
    payload = _json_response(config, "/v1/health", None, None)
    return {key: payload[key] for key in ("status", "version", "capabilities") if key in payload}


def transcribe(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    audio = _contained(request.get("audio_path"), config["readable"], "audio_path", True)
    if audio.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        raise SafeError("invalid_request", "audio_path has an unsupported audio extension.")
    if audio.stat().st_size > config["max_audio"]:
        raise SafeError("invalid_request", "Audio exceeds the configured size limit.")
    fields = {"responseFormat": "json"}
    for key in ("language", "profile"):
        if key in request:
            if not isinstance(request[key], str) or not request[key].strip():
                raise SafeError("invalid_request", f"{key} must be a non-empty string.")
            fields[key] = request[key]
    body, content_type = _multipart(audio, fields)
    payload = _json_response(config, "/v1/audio/transcriptions", body, content_type)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SafeError("empty_transcription", "EccoVox did not produce usable transcription text.")
    result = {"text": text}
    for source, target in (("language", "language"), ("confidence", "confidence"), ("durationMillis", "duration_millis"), ("metadata", "metadata")):
        if source in payload:
            result[target] = payload[source]
    return result


def synthesize(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if request.get("confirm") is not True:
        raise SafeError("confirmation_required", "tts.synthesize requires confirm: true because it writes an output file.")
    text = request.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > config["max_text"]:
        raise SafeError("invalid_request", "text must be non-empty and within the configured limit.")
    output = _contained(request.get("output_path"), config["writable"], "output_path", False)
    response_format = request.get("response_format", output.suffix.lower().lstrip("."))
    if response_format not in ALLOWED_TTS_FORMATS or output.suffix.lower() != f".{response_format}":
        raise SafeError("invalid_request", "output_path extension must match a supported response_format.")
    payload: dict[str, Any] = {"input": text, "responseFormat": response_format}
    for key in ("voice", "language", "profile"):
        if key in request:
            if not isinstance(request[key], str) or not request[key].strip():
                raise SafeError("invalid_request", f"{key} must be a non-empty string.")
            payload[key] = request[key]
    if "speed" in request:
        if not isinstance(request["speed"], (int, float)) or isinstance(request["speed"], bool):
            raise SafeError("invalid_request", "speed must be a number.")
        payload["speed"] = request["speed"]
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    audio, content_type = _request(config, "/v1/audio/speech", body, "application/json")
    if not audio:
        raise SafeError("runtime_error", "EccoVox returned an empty audio response.")
    expected = CONTENT_TYPES[response_format]
    if content_type not in expected:
        raise SafeError("runtime_error", "EccoVox returned an unexpected audio format.")
    output.write_bytes(audio)
    return {"output_path": str(output), "content_type": content_type, "bytes": len(audio)}


def execute(config: dict[str, Any], request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("version") != VERSION or not isinstance(request.get("operation"), str):
        raise SafeError("invalid_request", "Request must contain version: 1 and an operation.")
    operation = request["operation"]
    if operation == "health.get":
        return _success(operation, health(config))
    if operation == "stt.transcribe":
        return _success(operation, transcribe(config, request))
    if operation == "tts.synthesize":
        return _success(operation, synthesize(config, request))
    raise SafeError("unsupported_operation", "Unsupported operation.")


def main() -> int:
    parser = argparse.ArgumentParser(description="EccoVox JSON wrapper")
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config, args.profile)
        binary_stdin = getattr(sys.stdin, "buffer", None)
        raw = binary_stdin.read().decode("utf-8-sig") if binary_stdin is not None else sys.stdin.read().lstrip("\ufeff")
        request = json.loads(raw)
        result = execute(config, request)
    except json.JSONDecodeError:
        result = _error("invalid_request", "stdin must contain one valid JSON request.")
    except SafeError as exc:
        result = _error(exc.code, exc.message)
    except Exception:
        result = _error("internal_error", "Unexpected wrapper failure.")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
