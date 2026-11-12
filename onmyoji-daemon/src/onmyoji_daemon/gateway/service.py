"""Serviço HTTPS e RPC de publicação compartilhada da instância."""
from __future__ import annotations

import asyncio
import os
import ssl
import threading
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from .component.proxy import HttpProxy, MAX_BODY_BYTES
from .component.publication_test import PublicationTest
from .component.tls import ensure_material, server_context
from .exception import GatewayError
from .repository import ListenerRegistry
from ..config_publication import write_instance_text


class PublicGateway:
    """Mantém reservas duráveis e encaminha tráfego sem administrar o Tunnel.

    Somente o Supervisor, autenticado pelo token RPC privado deste serviço,
    fornece declarações e contexto público. Destinos retornam indisponíveis
    após reinício até a próxima declaração de uma geração viva. O teste público
    efêmero responde no próprio listener HTTPS, sem publicar consumidores.
    """

    def __init__(self, root: Path, data_dir: Path, token: str, http_port: int | None = None):
        self.root, self.data_dir, self.token = root.resolve(), data_dir.resolve(), token
        if not self.data_dir.is_relative_to(self.root / "configs"):
            raise GatewayError("invalid_state_directory", "O estado do Gateway deve permanecer em configs/.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "config.toml"
        self.http_port = http_port if http_port is not None else self._configuration()["http_port"]
        if type(self.http_port) is not int or not 0 <= self.http_port <= 65535:
            raise GatewayError("invalid_config", "Porta HTTPS inválida.")
        self.registry = ListenerRegistry(self.data_dir / "listeners.sqlite3")
        self.public_origin: str | None = None
        self.tunnel: dict[str, Any] = {}
        self.context_lock = threading.RLock()
        self.http_loop: asyncio.AbstractEventLoop | None = None
        self.http_thread: threading.Thread | None = None
        self.http_runner: web.AppRunner | None = None
        self.ready = False
        self.last_error: str | None = None
        self.proxy = HttpProxy(self.registry, self._origin, lambda: f"localhost:{self.http_port}")
        self.publication_test = PublicationTest()

    def _configuration(self) -> dict:
        try:
            result = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "http_port": 39390}
        except (OSError, ValueError):
            raise GatewayError("invalid_config", "Configuração do Gateway inválida.")
        if result.get("schema_version") != 1 or type(result.get("http_port")) is not int or not 1 <= result["http_port"] <= 65535:
            raise GatewayError("invalid_config", "Versão ou porta do Gateway inválida.")
        return result

    def configure(self, params: dict) -> dict:
        """Persiste a porta desejada; alterações entram em vigor após reinício."""
        if params.get("confirm") is not True:
            raise GatewayError("confirmation_required", "Configurar exige confirm=true.")
        port = params.get("http_port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise GatewayError("invalid_config", "Porta HTTPS inválida.")
        write_instance_text(self.root, self.config_path, f"schema_version = 1\nhttp_port = {port}\n")
        return {"http_port": port, "restart_required": port != self.http_port}

    def _origin(self) -> str | None:
        with self.context_lock:
            return self.public_origin

    def context(self, params: dict) -> dict:
        """Recebe a origem e o estado sanitizado do Tunnel fornecidos pelo Supervisor."""
        origin = params.get("public_origin")
        if origin is not None:
            try:
                parsed = urlsplit(origin) if isinstance(origin, str) else None
                valid = parsed and not any(ord(char) <= 32 or ord(char) == 127 for char in origin) and parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment and parsed.port in {None, 443}
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise GatewayError("invalid_origin", "Origem pública inválida.")
            origin = origin.rstrip("/")
        tunnel = params.get("tunnel", {})
        if not isinstance(tunnel, dict):
            raise GatewayError("invalid_context", "Estado público inválido.")
        # The context is operational state, never a transport for credentials or
        # arbitrary errors returned by a remote API.
        fields = {"configured", "provisioned", "running", "connected", "ingress_ready", "hostname", "cloudflared_available", "last_error", "state"}
        with self.context_lock:
            if origin != self.public_origin:
                self.publication_test.close()
            self.public_origin = origin
            self.tunnel = {key: value for key, value in tunnel.items() if key in fields and isinstance(value, (str, bool, int, type(None)))}
        return self.status()

    def status(self) -> dict:
        ca_file = self.data_dir / "tls" / "ca.pem"
        listeners = self.registry.list()
        with self.context_lock:
            return {"service": "gateway", "ready": self.ready, "http_url": f"https://localhost:{self.http_port}", "http_port": self.http_port, "ca_file": str(ca_file), "public_origin": self.public_origin, "tunnel": dict(self.tunnel), "listeners": listeners, "publications": listeners, "last_error": self.last_error}

    def _validate_ca_files(self, listeners: Any) -> None:
        if not isinstance(listeners, list):
            return
        for item in listeners:
            ca = item.get("ca_file") if isinstance(item, dict) else None
            target = item.get("target") if isinstance(item, dict) else None
            if isinstance(target, str) and target.partition(":")[0].lower() == "https" and ca is None:
                raise GatewayError("invalid_ca", "Destinos HTTPS exigem uma CA explícita.")
            if ca is not None:
                if not isinstance(ca, str) or not Path(ca).is_absolute() or not Path(ca).resolve().is_relative_to(self.root / "configs") or not Path(ca).is_file():
                    raise GatewayError("invalid_ca", "A CA do destino deve ser um arquivo local de configs/.")
                try:
                    ssl.create_default_context(cafile=ca)
                except (OSError, ValueError):
                    raise GatewayError("invalid_ca", "A CA do destino não contém certificados válidos.") from None

    def rpc(self, method: str, params: dict[str, Any]) -> Any:
        """Administra o Gateway e a tentativa efêmera de acesso público.

        Iniciar o teste exige confirmação (``confirmation_required``) e origem,
        HTTPS e Tunnel prontos (``publication_unavailable``). Consultar ou cancelar
        uma sessão desconhecida resulta em ``test_not_found``.
        """
        action = method.removeprefix("gateway.")
        if action == "status":
            return self.status()
        if action == "configure":
            return self.configure(params)
        if action == "publication-test.start":
            if params.get("confirm") is not True:
                raise GatewayError("confirmation_required", "Criar o teste temporário exige confirm=true.")
            with self.context_lock:
                if not self.ready or not self.public_origin or not all(self.tunnel.get(field) is True for field in ("configured", "provisioned", "running", "connected", "ingress_ready")):
                    raise GatewayError("publication_unavailable", "Teste indisponível: inicie Gateway e Tunnel, conclua o provisionamento e aguarde a conexão em Publicação externa.")
                return self.publication_test.start(self.public_origin)
        if action == "publication-test.status":
            return self.publication_test.status(params.get("test_id"))
        if action == "publication-test.cancel":
            return self.publication_test.cancel(params.get("test_id"))
        if action == "publication.context":
            return self.context(params)
        if action == "publication.sync":
            self._validate_ca_files(params.get("listeners"))
            return {"listeners": self.registry.sync(params.get("owner"), params.get("generation"), params.get("listeners"), params.get("available", True))}
        if action == "publication.unavailable":
            return {"listeners": self.registry.unavailable(params.get("owner"), params.get("generation"))}
        if action == "listeners.list":
            return {"listeners": self.registry.list(params.get("owner"))}
        if action == "listeners.inspect":
            return self.registry.inspect(params.get("path_prefix"), params.get("owner"))
        if action in {"listeners.enable", "listeners.disable", "listeners.remove", "listeners.restore"}:
            return self.registry.change(action.split(".")[1], params.get("path_prefix"), params.get("owner"), params.get("confirm", False))
        raise GatewayError("unsupported_operation", "Operação do Gateway desconhecida.")

    async def _http_main(self) -> None:
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.on_startup.append(self.proxy.start)
        app.on_cleanup.append(self.proxy.close)
        async def handle(request):
            if request.raw_path.startswith(PublicationTest.PATH_PREFIX):
                return await self.publication_test.handle(request)
            return await self.proxy.handle(request)
        app.router.add_route("*", "/{tail:.*}", handle)
        ensure_material(self.data_dir / "tls")
        runner = web.AppRunner(app, access_log=None, auto_decompress=False)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", self.http_port, ssl_context=server_context(self.data_dir / "tls"))
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        if self.http_port == 0:
            self.http_port = site._server.sockets[0].getsockname()[1]
        self.http_runner, self.ready = runner, True

    def start_http(self) -> None:
        """Inicia HTTPS em loopback ou falha sem deixar um serviço aparentemente pronto."""
        if self.ready:
            return
        started = threading.Event()
        def worker():
            self.http_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.http_loop)
            try:
                self.http_loop.run_until_complete(self._http_main())
            except Exception:
                self.last_error = "O listener HTTPS não iniciou; verifique a porta e os certificados."
                started.set()
                self.http_loop.close()
                return
            started.set()
            try:
                self.http_loop.run_forever()
            finally:
                pending = asyncio.all_tasks(self.http_loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.http_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self.http_loop.close()
        self.http_thread = threading.Thread(target=worker, name="onmyoji-public-gateway", daemon=True)
        self.http_thread.start()
        if not started.wait(10) or not self.ready:
            raise GatewayError("http_start_failed", self.last_error or "O listener HTTPS não iniciou.")

    def stop_http(self) -> None:
        with self.context_lock:
            self.ready = False
            self.publication_test.close()
        if self.http_loop and self.http_loop.is_running():
            if self.http_runner:
                asyncio.run_coroutine_threadsafe(self.http_runner.cleanup(), self.http_loop).result(10)
            self.http_loop.call_soon_threadsafe(self.http_loop.stop)
        if self.http_thread:
            self.http_thread.join(10)
        self.http_runner = None

    def close(self) -> None:
        self.stop_http()
        self.registry.close()
