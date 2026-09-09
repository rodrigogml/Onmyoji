"""Cloudflare Named Tunnel para o Gateway de Mini Apps.

Este módulo não lê nem grava segredos. O setup obtém o token do KeePass e o
entrega somente em memória para ``TunnelProcess`` ao iniciar cloudflared.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from typing import Any
import urllib.error
import urllib.request
import tomllib


class TunnelError(RuntimeError): pass


@dataclass(frozen=True)
class TunnelDefinition:
    account_id: str
    zone_id: str
    hostname: str
    tunnel_id: str = ""
    tunnel_name: str = "onmyoji-mini-apps"

    def validate(self) -> None:
        if not self.account_id or not self.zone_id: raise TunnelError("account_id e zone_id são obrigatórios")
        if "." not in self.hostname or self.hostname.startswith("."): raise TunnelError("hostname público inválido")


class CloudflareClient:
    """Cliente mínimo para Tunnel e DNS; toda escrita exige confirmação."""
    def __init__(self, token: str, base_url: str = "https://api.cloudflare.com/client/v4", timeout: float = 30):
        if not token: raise TunnelError("token Cloudflare não informado")
        self.token, self.base_url, self.timeout = token, base_url.rstrip("/"), timeout

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=payload, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response: value = json.loads(response.read())
        except urllib.error.HTTPError as error: raise TunnelError(f"Cloudflare HTTP {error.code}") from error
        except (OSError, ValueError) as error: raise TunnelError("falha de comunicação Cloudflare") from error
        if not value.get("success"): raise TunnelError("Cloudflare recusou a operação")
        return value.get("result")

    def create_tunnel(self, definition: TunnelDefinition, confirm: bool) -> dict[str, Any]:
        definition.validate()
        if not confirm: raise TunnelError("criação de tunnel requer confirmação")
        result = self._call("POST", f"/accounts/{definition.account_id}/cfd_tunnel", {"name": definition.tunnel_name, "config_src": "cloudflare"})
        return dict(result)

    def configure_ingress(self, definition: TunnelDefinition, tunnel_id: str, local_url: str, confirm: bool) -> None:
        definition.validate()
        if not confirm: raise TunnelError("configuração de ingress requer confirmação")
        # O certificado local e emitido pela CA privada da instancia. O
        # cloudflared e o unico cliente deste hop de loopback; desabilitar a
        # verificacao aqui nao reduz a TLS publica, que continua terminada na
        # Cloudflare. Isso evita exigir que o processo cloudflared remoto
        # conheca uma CA que nunca sai da maquina local.
        config = {"config": {"ingress": [{"hostname": definition.hostname, "service": local_url, "originRequest": {"noTLSVerify": True}}, {"service": "http_status:404"}]}}
        self._call("PUT", f"/accounts/{definition.account_id}/cfd_tunnel/{tunnel_id}/configurations", config)

    def ensure_dns(self, definition: TunnelDefinition, tunnel_id: str, confirm: bool) -> dict[str, Any]:
        definition.validate()
        if not confirm: raise TunnelError("alteração DNS requer confirmação")
        target = f"{tunnel_id}.cfargotunnel.com"
        records = self._call("GET", f"/zones/{definition.zone_id}/dns_records?type=CNAME&name={definition.hostname}") or []
        body = {"type": "CNAME", "name": definition.hostname, "content": target, "proxied": True}
        if records:
            return dict(self._call("PUT", f"/zones/{definition.zone_id}/dns_records/{records[0]['id']}", body))
        return dict(self._call("POST", f"/zones/{definition.zone_id}/dns_records", body))

    def status(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        return dict(self._call("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}"))


class TunnelProcess:
    def __init__(self, executable: str, token: str):
        self.executable, self.token, self.process = executable, token, None
        if not shutil.which(executable) and not Path(executable).is_file(): raise TunnelError("cloudflared não encontrado")

    def start(self) -> None:
        if self.process and self.process.poll() is None: return
        # cloudflared exige o token como argumento no modo remotely-managed;
        # nunca o registramos em configuração ou logs do Onmyoji.
        self.process = subprocess.Popen([self.executable, "tunnel", "run", "--token", self.token], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None: return
        self.process.terminate()
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)

    def status(self) -> dict[str, Any]:
        return {"running": bool(self.process and self.process.poll() is None), "pid": self.process.pid if self.process and self.process.poll() is None else None}


class TunnelController:
    """Configuração privada, provisionamento confirmado e processo local."""
    def __init__(self, root: Path, data_dir: Path, local_url: str):
        self.root, self.path, self.local_url, self.process = root, data_dir / "tunnel.toml", local_url, None

    def settings(self) -> dict[str, Any]:
        try: value = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError: return {}
        except tomllib.TOMLDecodeError as error: raise TunnelError("configuração de tunnel inválida") from error
        return value if value.get("schema_version") == 1 else {}

    def configure(self, values: dict[str, Any]) -> dict[str, Any]:
        required = ("account_id", "zone_id", "hostname", "keepass_profile", "token_entry")
        if any(not str(values.get(key) or "").strip() for key in required): raise TunnelError("configuração de tunnel incompleta")
        definition = TunnelDefinition(*(str(values[key]).strip() for key in ("account_id", "zone_id", "hostname")), str(values.get("tunnel_id") or ""), str(values.get("tunnel_name") or "onmyoji-mini-apps")); definition.validate()
        executable = str(values.get("cloudflared_executable") or "cloudflared")
        # JSON string literals tambem sao TOML basic strings e lidam
        # corretamente com aspas, barras e caracteres de escape do Windows.
        encode = lambda value: json.dumps(str(value), ensure_ascii=False)
        content = "\n".join(["schema_version = 1", f"account_id = {encode(definition.account_id)}", f"zone_id = {encode(definition.zone_id)}", f"hostname = {encode(definition.hostname)}", f"tunnel_id = {encode(definition.tunnel_id)}", f"tunnel_name = {encode(definition.tunnel_name)}", f"keepass_profile = {encode(values['keepass_profile'])}", f"token_entry = {encode(values['token_entry'])}", f"cloudflared_executable = {encode(executable)}", ""])
        self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(content, encoding="utf-8", newline="\n")
        return self.status()

    def _token(self, settings: dict[str, Any]) -> str:
        wrapper = self.root / "available-skills" / "keepass-vault" / "scripts" / "keepass_vault.py"
        request = {"operation": "read", "entry": {"path": settings["token_entry"]}, "field": "password"}
        result = subprocess.run([str(__import__("sys").executable), str(wrapper), "--config", str(self.root / "configs" / "keepass.toml"), "--profile", str(settings["keepass_profile"])], input=json.dumps(request), text=True, capture_output=True, timeout=45)
        try: payload = json.loads(result.stdout); token = payload["result"]["value"]
        except (ValueError, KeyError, TypeError) as error: raise TunnelError("KeePass não retornou token de Tunnel") from error
        if not payload.get("ok") or not isinstance(token, str) or not token: raise TunnelError("KeePass não retornou token de Tunnel")
        return token

    def provision(self, confirm: bool) -> dict[str, Any]:
        settings = self.settings()
        if not settings: raise TunnelError("tunnel não configurado")
        definition = TunnelDefinition(settings["account_id"], settings["zone_id"], settings["hostname"], settings.get("tunnel_id", ""), settings["tunnel_name"]); client = CloudflareClient(self._token(settings))
        if not definition.tunnel_id:
            created = client.create_tunnel(definition, confirm); settings["tunnel_id"] = str(created["id"]); self.configure(settings); definition = TunnelDefinition(definition.account_id, definition.zone_id, definition.hostname, settings["tunnel_id"], definition.tunnel_name)
        client.configure_ingress(definition, definition.tunnel_id, self.local_url, confirm); client.ensure_dns(definition, definition.tunnel_id, confirm)
        return self.status()

    def start(self) -> dict[str, Any]:
        settings = self.settings()
        if not settings or not settings.get("tunnel_id"): raise TunnelError("tunnel ainda não foi provisionado")
        self.process = TunnelProcess(str(settings["cloudflared_executable"]), self._token(settings)); self.process.start(); return self.status()

    @staticmethod
    def cloudflared_download_url(system: str | None = None, machine: str | None = None) -> str:
        system = (system or platform.system()).casefold(); machine = (machine or platform.machine()).casefold()
        arch = "amd64" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else ""
        if not arch: raise TunnelError("arquitetura sem instalador cloudflared suportado")
        if system == "windows": return f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-{arch}.exe"
        if system == "linux": return f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
        raise TunnelError("sistema sem instalador cloudflared suportado")

    def install_cloudflared(self, confirm: bool) -> dict[str, Any]:
        if not confirm: raise TunnelError("download de cloudflared requer confirmação")
        settings = self.settings()
        if not settings: raise TunnelError("configure o tunnel antes de instalar cloudflared")
        suffix = ".exe" if os.name == "nt" else ""
        destination = self.path.parent / "bin" / f"cloudflared{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".download")
        try:
            with urllib.request.urlopen(self.cloudflared_download_url(), timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if temporary.stat().st_size < 1_000_000: raise TunnelError("download cloudflared inválido")
            temporary.replace(destination)
            if os.name != "nt": destination.chmod(destination.stat().st_mode | 0o111)
        except (OSError, urllib.error.URLError) as error:
            temporary.unlink(missing_ok=True); raise TunnelError("não foi possível obter cloudflared oficial") from error
        settings["cloudflared_executable"] = str(destination)
        self.configure(settings)
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self.process: self.process.stop()
        return self.status()

    def status(self) -> dict[str, Any]:
        values = self.settings(); state = self.process.status() if self.process else {"running": False, "pid": None}
        executable = str(values.get("cloudflared_executable", "cloudflared"))
        found = bool(shutil.which(executable) or Path(executable).is_file())
        return {"configured": bool(values), "provisioned": bool(values.get("tunnel_id")) if values else False, "hostname": values.get("hostname") if values else None, "cloudflared_executable": executable, "cloudflared_available": found, **state}
