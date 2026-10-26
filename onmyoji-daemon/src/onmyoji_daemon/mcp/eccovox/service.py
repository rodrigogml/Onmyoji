"""Cliente seguro do runtime EccoVox usado pelo MCP e pelos gateways."""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import secrets
import tomllib
from typing import Any, Mapping
from urllib import error, parse, request


ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".webm", ".flac"}
ALLOWED_TTS_FORMATS = {"mp3", "wav", "opus", "flac"}
CONTENT_TYPES = {"mp3": {"audio/mpeg", "audio/mp3"}, "wav": {"audio/wav", "audio/x-wav"}, "opus": {"audio/ogg", "audio/opus"}, "flac": {"audio/flac"}}


class EccoVoxError(ValueError):
    """Erro sanitizado da integração EccoVox."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        raise EccoVoxError("runtime_error", "EccoVox retornou um redirecionamento inesperado.")


class EccoVoxService:
    """Executa STT e TTS somente contra um endpoint HTTP local configurado."""

    def __init__(self, onmyoji_root: Path):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "eccovox.toml"

    def health(self, profile_name: str) -> dict[str, Any]:
        config = self._profile(profile_name)
        payload = self._json(config, "/v1/health", None, None)
        return {key: payload[key] for key in ("status", "version", "capabilities") if key in payload}

    def transcribe(self, profile_name: str, audio_path: str, *, language: str | None = None, runtime_profile: str | None = None) -> dict[str, Any]:
        config = self._profile(profile_name)
        audio = self._contained(audio_path, config["readable_roots"], "audio_path", True)
        if audio.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
            raise EccoVoxError("invalid_request", "O formato do áudio não é suportado.")
        if audio.stat().st_size > config["max_audio_bytes"]:
            raise EccoVoxError("invalid_request", "O áudio excede o limite configurado.")
        fields = {"responseFormat": "json"}
        for key, value in (("language", language), ("profile", runtime_profile)):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise EccoVoxError("invalid_request", f"{key} deve ser informado quando usado.")
                fields[key] = value
        body, content_type = self._multipart(audio, fields)
        payload = self._json(config, "/v1/audio/transcriptions", body, content_type)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise EccoVoxError("empty_transcription", "EccoVox não produziu uma transcrição utilizável.")
        result = {"text": text}
        for source, target in (("language", "language"), ("confidence", "confidence"), ("durationMillis", "duration_millis"), ("metadata", "metadata")):
            if source in payload:
                result[target] = payload[source]
        return result

    def synthesize(self, profile_name: str, text: str, output_path: str, response_format: str | None = None, *, voice: str | None = None, language: str | None = None, speed: float | int | None = None, runtime_profile: str | None = None) -> dict[str, Any]:
        config = self._profile(profile_name)
        if not isinstance(text, str) or not text.strip() or len(text) > config["max_text_characters"]:
            raise EccoVoxError("invalid_request", "O texto deve ser preenchido e respeitar o limite configurado.")
        output = self._contained(output_path, config["writable_roots"], "output_path", False)
        media_format = response_format or output.suffix.lower().lstrip(".")
        if media_format not in ALLOWED_TTS_FORMATS or output.suffix.lower() != f".{media_format}":
            raise EccoVoxError("invalid_request", "A extensão do arquivo deve corresponder a um formato de áudio suportado.")
        payload: dict[str, Any] = {"input": text, "responseFormat": media_format}
        for key, value in (("voice", voice), ("language", language), ("profile", runtime_profile)):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise EccoVoxError("invalid_request", f"{key} deve ser informado quando usado.")
                payload[key] = value
        if speed is not None:
            if not isinstance(speed, (int, float)) or isinstance(speed, bool):
                raise EccoVoxError("invalid_request", "speed deve ser numérico.")
            payload["speed"] = speed
        audio, content_type = self._request(config, "/v1/audio/speech", json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")
        if not audio or content_type not in CONTENT_TYPES[media_format]:
            raise EccoVoxError("runtime_error", "EccoVox retornou um áudio inválido.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(audio)
        return {"output_path": str(output), "content_type": content_type, "bytes": len(audio)}

    def _profile(self, name: str) -> dict[str, Any]:
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
            profile = data.get("profiles", {}).get(name)
            defaults = data.get("defaults", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise EccoVoxError("invalid_config", "Configuração MCP do EccoVox indisponível.") from exc
        if not isinstance(profile, dict) or profile.get("enabled") is not True or not isinstance(defaults, dict):
            raise EccoVoxError("profile_unavailable", "Perfil EccoVox indisponível.")
        try:
            base_url = str(profile["base_url"]).strip()
            parsed = parse.urlsplit(base_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError
            timeout, max_audio, max_text = int(defaults["request_timeout_seconds"]), int(defaults["max_audio_bytes"]), int(defaults["max_text_characters"])
            readable = self._roots(profile["readable_roots"]); writable = self._roots(profile["writable_roots"])
            if not 1 <= timeout <= 300 or not 1 <= max_audio <= 104857600 or not 1 <= max_text <= 100000:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise EccoVoxError("invalid_config", "Perfil EccoVox inválido.") from exc
        return {"base_url": base_url.rstrip("/"), "timeout": timeout, "max_audio_bytes": max_audio, "max_text_characters": max_text, "readable_roots": readable, "writable_roots": writable}

    @staticmethod
    def _roots(values: Any) -> tuple[Path, ...]:
        if not isinstance(values, list):
            raise ValueError
        roots = tuple(Path(value).expanduser().resolve() for value in values if isinstance(value, str) and value.strip())
        if len(roots) != len(values) or any(not root.is_dir() for root in roots):
            raise ValueError
        return roots

    @staticmethod
    def _contained(value: str, roots: tuple[Path, ...], field: str, must_exist: bool) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise EccoVoxError("invalid_request", f"{field} é obrigatório.")
        if not must_exist and Path(value).is_symlink():
            raise EccoVoxError("path_not_allowed", f"{field} não pode ser um link simbólico.")
        path = Path(value).resolve()
        if must_exist and not path.is_file():
            raise EccoVoxError("file_not_found", f"{field} deve identificar um arquivo existente.")
        if not roots or not any(path.is_relative_to(root) for root in roots):
            raise EccoVoxError("path_not_allowed", f"{field} está fora das raízes autorizadas.")
        return path

    @staticmethod
    def _multipart(audio: Path, fields: Mapping[str, str]) -> tuple[bytes, str]:
        boundary = "----EccoVox" + secrets.token_hex(16); chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode(), b"\r\n"))
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), audio.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()))
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def _json(self, config: dict[str, Any], endpoint: str, body: bytes | None, content_type: str | None) -> dict[str, Any]:
        raw, response_type = self._request(config, endpoint, body, content_type)
        if response_type != "application/json":
            raise EccoVoxError("runtime_error", "EccoVox retornou uma resposta inesperada.")
        try: payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise EccoVoxError("runtime_error", "EccoVox retornou JSON inválido.") from exc
        if not isinstance(payload, dict): raise EccoVoxError("runtime_error", "EccoVox retornou uma resposta inválida.")
        return payload

    @staticmethod
    def _request(config: dict[str, Any], endpoint: str, body: bytes | None, content_type: str | None) -> tuple[bytes, str]:
        headers = {"Accept": "application/json"}
        if content_type: headers["Content-Type"] = content_type
        query = request.Request(config["base_url"] + endpoint, data=body, headers=headers, method="POST" if body is not None else "GET")
        try:
            with request.build_opener(_NoRedirect).open(query, timeout=config["timeout"]) as response:
                return response.read(), response.headers.get_content_type()
        except error.HTTPError as exc: raise EccoVoxError("runtime_error", f"EccoVox rejeitou a solicitação ({exc.code}).") from exc
        except error.URLError as exc: raise EccoVoxError("runtime_unavailable", "EccoVox está indisponível.") from exc
        except TimeoutError as exc: raise EccoVoxError("timeout", "A solicitação ao EccoVox excedeu o tempo limite.") from exc
