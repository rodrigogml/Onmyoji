"""Contrato único para iniciar o Codex em uma instância Onmyōji.

Os clientes direto, interativo e de gateway têm protocolos próprios, mas todos
derivam desta especificação: executável, ``CODEX_HOME``, workspace, política e
registro MCP. Nenhum consumidor deve montar esses valores isoladamente.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from .management import daemon_python, runtime_ready
from .mcp.codex import CodexMcpRegistration
from .mcp.configuration import McpConfiguration
from .mcp.data_tables import ManagedDataTablesSkill
from .mcp.memory import ManagedMemorySkill
from .mcp.reconciliation import McpReconciliation
from .shikigami_skills import ShikigamiSkillMaterializer


class CodexExecutionError(RuntimeError):
    """Indica que não é seguro iniciar o Codex com a configuração atual."""


@dataclass(frozen=True)
class CodexLaunch:
    """Especificação normalizada usada por todas as aberturas do Codex.

    As propriedades de modelo, esforço, sandbox e aprovação são mantidas aqui
    mesmo quando o protocolo App Server as transmite depois do processo iniciar.
    """

    root: Path
    executable: str
    workspace: Path
    model: str
    effort: str
    sandbox: str
    approval: str
    writable_directories: tuple[str, ...]

    @classmethod
    def from_data(cls, root: Path, data: Mapping[str, object]) -> "CodexLaunch":
        """Cria a especificação a partir da configuração já validada pelo setup."""
        root = root.resolve()
        workspace = Path(str(data.get("project_directory") or "")).expanduser()
        if not workspace.is_absolute():
            workspace = root / workspace
        directories = data.get("additional_writable_directories", [])
        if not isinstance(directories, list) or not all(isinstance(value, str) for value in directories):
            raise CodexExecutionError("Diretórios adicionais do Codex são inválidos.")
        executable = str(data.get("executable") or "").strip()
        if not executable or not workspace.is_dir():
            raise CodexExecutionError("Executável ou workspace do Codex não está configurado.")
        return cls(root, executable, workspace.resolve(), str(data.get("model") or ""), str(data.get("model_reasoning_effort") or ""), str(data.get("sandbox_mode") or ""), str(data.get("approval_policy") or ""), tuple(directories))

    @classmethod
    def from_system(cls, root: Path) -> "CodexLaunch":
        """Lê a fonte local de verdade compartilhada pelos processos do daemon."""
        import tomllib

        try:
            document = tomllib.loads((root.resolve() / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
            data = document.get("codex", {})
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise CodexExecutionError("Configuração local do Codex não está disponível.") from error
        if not isinstance(data, dict):
            raise CodexExecutionError("Configuração local do Codex é inválida.")
        return cls.from_data(root, data)

    def environment(self, *, daemon_source: bool = False) -> dict[str, str]:
        """Gera o ambiente comum; ``daemon_source`` habilita o launcher local."""
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(self.root)
        if daemon_source:
            source = str(Path(__file__).resolve().parents[1])
            inherited = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = source + (os.pathsep + inherited if inherited else "")
        return environment

    def direct_command(self) -> list[str]:
        """Monta o cliente interativo nativo com as mesmas raízes declaradas."""
        command = [self.executable]
        for directory in self.writable_directories:
            command.extend(["--add-dir", directory])
        return command

    def direct_launch(self) -> tuple[list[str], Path, dict[str, str]]:
        """Prepara o cliente direto e reconcilia o MCP quando houver pendência."""
        self._reconcile_mcp()
        return self.direct_command(), self.workspace, self.environment()

    def app_server_launch(self) -> tuple[list[str], Path, dict[str, str]]:
        """Prepara o App Server e reconcilia o MCP antes de iniciá-lo."""
        self._reconcile_mcp()
        return [self.executable, "app-server", "--stdio"], self.workspace, self.environment()

    def _reconcile_mcp(self) -> None:
        """Aplica uma pendência MCP sem exigir reinicialização do gateway."""
        try:
            self.synchronize_managed_skills()
            McpReconciliation(self.root).reconcile_if_pending(CodexMcpRegistration(self.root, self.executable))
        except Exception as error:
            raise CodexExecutionError(str(error)) from error

    def synchronize_memory_skill(self) -> bool:
        """Materializa ou repara a skill local conforme o único estado MCP."""
        try:
            configuration = McpConfiguration(self.root)
            return ManagedMemorySkill(self.root).synchronize(self.workspace, configuration.memory_tool_available())
        except Exception as error:
            raise CodexExecutionError(str(error)) from error

    def synchronize_managed_skills(self) -> bool:
        """Mantém no workspace todos os workflows canônicos habilitados."""
        try:
            configuration = McpConfiguration(self.root)
            memory_changed = ManagedMemorySkill(self.root).synchronize(self.workspace, configuration.memory_tool_available())
            tables_changed = ManagedDataTablesSkill(self.root).synchronize(self.workspace, configuration.data_tables_tool_available())
            shikigami_changed = ShikigamiSkillMaterializer(self.root).synchronize(self.workspace)
            return memory_changed or tables_changed or shikigami_changed
        except Exception as error:
            raise CodexExecutionError(str(error)) from error

    def interactive_launcher(self) -> tuple[list[str], Path, dict[str, str]]:
        """Prepara o console Onmyōji no mesmo runtime que atende o gateway."""
        if not runtime_ready(self.root):
            raise CodexExecutionError("Runtime do daemon não está pronto para iniciar o console Onmyōji.")
        return [str(daemon_python(self.root)), "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(self.root), "interactive"], self.workspace, self.environment(daemon_source=True)
