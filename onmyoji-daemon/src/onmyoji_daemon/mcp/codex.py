"""Registro seguro do servidor MCP local para consumidores Codex.

O registro é mantido pelo próprio Codex CLI em ``CODEX_HOME/config.toml``. Este
módulo só administra a entrada nomeada ``onmyoji``: nunca regrava o arquivo nem
altera servidores de terceiros. O setup pode substituir explicitamente a entrada
gerenciada; o gateway apenas cria uma entrada ausente e recusa conflitos.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .runtime import McpRuntimeError, ensure_mcp_runtime, mcp_runtime_python


SERVER_NAME = "onmyoji"


class CodexMcpError(RuntimeError):
    """Indica uma condição segura de configuração entre Onmyōji e Codex."""


@dataclass(frozen=True)
class CodexMcpRegistration:
    """Garante a configuração stdio do MCP para uma instância Onmyōji.

    O processo MCP usa seu runtime privado em ``configs/mcp/venv`` e recebe
    apenas a raiz da instância e o código-fonte por ambiente. Credenciais
    continuam exclusivamente nos perfis em ``configs/mcp``.
    """

    root: Path
    codex_executable: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    @property
    def source_directory(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def expected_command(self) -> list[str]:
        return [str(mcp_runtime_python(self.root)), "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(self.root)]

    def prepare_runtime(self) -> str:
        """Prepara o runtime MCP sem depender da instalação do daemon."""
        try:
            return ensure_mcp_runtime(self.root)
        except McpRuntimeError as error:
            raise CodexMcpError(str(error)) from error

    def ensure(self) -> str:
        """Cria o registro ausente ou confirma sua configuração sem sobrescrever."""
        self._validate_prerequisites()
        current = self._current()
        if current is None:
            self._add()
            return "Servidor MCP Onmyōji registrado para o Codex CLI."
        if not self._matches(current):
            raise CodexMcpError(
                "Já existe um servidor MCP chamado 'onmyoji' com configuração diferente. "
                "Use a opção de atualizar registro no setup para substituí-lo conscientemente."
            )
        return "Registro MCP Onmyōji já está válido para o Codex CLI."

    def replace(self) -> str:
        """Substitui, por ação explícita do operador, apenas o registro Onmyōji."""
        self._validate_prerequisites()
        if self._current() is not None:
            completed = self._run("mcp", "remove", SERVER_NAME)
            if completed.returncode != 0:
                raise CodexMcpError("Não foi possível remover o registro MCP Onmyōji anterior.")
        self._add()
        return "Registro MCP Onmyōji atualizado para o runtime MCP atual."

    def reconcile(self) -> str:
        """Corrige automaticamente apenas o registro desta instância.

        A chamada ocorre sob a trava persistida do coordenador MCP, antes de
        uma nova abertura do Codex. Como ``CODEX_HOME`` é exclusivo da
        instância, o nome ``onmyoji`` pertence a este gerenciamento local.
        """
        self._validate_prerequisites()
        current = self._current()
        if current is None:
            self._add()
            return "Servidor MCP Onmyōji registrado para o Codex CLI."
        if self._matches(current):
            return "Registro MCP Onmyōji já está válido para o Codex CLI."
        completed = self._run("mcp", "remove", SERVER_NAME)
        if completed.returncode != 0:
            raise CodexMcpError("Não foi possível corrigir o registro MCP Onmyōji anterior.")
        self._add()
        return "Registro MCP Onmyōji reconciliado para o runtime MCP atual."

    def remove(self) -> str:
        """Remove somente o registro MCP pertencente a esta instância."""
        if self._current() is None:
            return "Nenhum registro MCP Onmyōji foi encontrado nesta instância."
        completed = self._run("mcp", "remove", SERVER_NAME)
        if completed.returncode != 0:
            raise CodexMcpError("Não foi possível remover o registro MCP Onmyōji.")
        return "Registro MCP Onmyōji removido do Codex CLI."

    def status(self) -> str:
        """Retorna um diagnóstico sem exibir parâmetros ou variáveis sensíveis."""
        try:
            current = self._current()
        except CodexMcpError as error:
            return str(error)
        if current is None:
            return "O servidor MCP Onmyōji ainda não está registrado no Codex CLI."
        return "Registro MCP Onmyōji válido para o Codex CLI." if self._matches(current) else "O registro MCP Onmyōji existe, mas não corresponde ao runtime atual do daemon."

    def _validate_prerequisites(self) -> None:
        if not self.codex_executable:
            raise CodexMcpError("Executável do Codex CLI não está configurado.")
        self.prepare_runtime()

    def _current(self) -> dict[str, Any] | None:
        completed = self._run("mcp", "list", "--json")
        if completed.returncode != 0:
            raise CodexMcpError("Não foi possível consultar os servidores MCP configurados no Codex CLI.")
        try:
            items = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CodexMcpError("O Codex CLI retornou uma lista MCP inválida.") from error
        if not isinstance(items, list):
            raise CodexMcpError("O Codex CLI retornou uma lista MCP inválida.")
        return next((item for item in items if isinstance(item, dict) and item.get("name") == SERVER_NAME), None)

    def _matches(self, current: dict[str, Any]) -> bool:
        transport = current.get("transport")
        if not isinstance(transport, dict) or transport.get("type") != "stdio":
            return False
        command = self.expected_command()
        if str(transport.get("command") or "") != command[0] or transport.get("args") != command[1:]:
            return False
        environment = transport.get("env")
        return isinstance(environment, dict) and environment.get("PYTHONPATH") == str(self.source_directory)

    def _add(self) -> None:
        command = self.expected_command()
        completed = self._run("mcp", "add", SERVER_NAME, "--env", f"PYTHONPATH={self.source_directory}", "--", *command)
        if completed.returncode != 0:
            raise CodexMcpError("Não foi possível registrar o servidor MCP Onmyōji no Codex CLI.")

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(self.root)
        try:
            return subprocess.run(
                [self.codex_executable, *arguments], cwd=str(self.root), env=environment,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CodexMcpError("Não foi possível iniciar o Codex CLI para configurar o MCP.") from error
