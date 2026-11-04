"""Instala versões oficiais verificadas sem sobrescrever um executável anterior."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable
import urllib.request

from .configuration import TunnelConfiguration
from .exception import TunnelError


RELEASE_URL = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"


def _system_error_detail(error: BaseException) -> str:
    """Retorna classe e código operacional, sem caminhos ou saída de subprocessos."""
    code = getattr(error, "winerror", None)
    if code is not None:
        return f"{type(error).__name__}, WinError {code}"
    code = getattr(error, "errno", None)
    if code is not None:
        return f"{type(error).__name__}, errno {code}"
    if isinstance(error, subprocess.TimeoutExpired):
        return f"tempo limite de {error.timeout} segundos excedido"
    return type(error).__name__


def asset_name(system: str | None = None, machine: str | None = None) -> str:
    system, machine = (system or platform.system()).lower(), (machine or platform.machine()).lower()
    arch = "amd64" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else ""
    if system == "windows" and arch == "amd64":
        return "cloudflared-windows-amd64.exe"
    if system == "linux" and arch:
        return f"cloudflared-linux-{arch}"
    raise TunnelError("unsupported_platform", "Instalação suporta Windows amd64 e Linux amd64/arm64.")


class CloudflaredInstaller:
    def __init__(self, configuration: TunnelConfiguration):
        self.configuration = configuration

    @staticmethod
    def _release(version: str | None = None) -> dict[str, Any]:
        if version is not None and (not isinstance(version, str) or not re.fullmatch(r"v?\d{4}\.\d+\.\d+", version)):
            raise TunnelError("invalid_release", "Identificador da versão oficial inválido.")
        url = RELEASE_URL if version is None else RELEASE_URL.removesuffix("latest") + "tags/" + version
        request = urllib.request.Request(url, headers={"User-Agent": "Onmyoji-Daemon", "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.loads(response.read())
        except (OSError, ValueError) as error:
            raise TunnelError("release_unavailable", f"Não foi possível consultar a versão oficial do cloudflared ({_system_error_detail(error)}).") from error
        if not isinstance(value, dict):
            raise TunnelError("invalid_release", "Metadados da versão oficial inválidos.")
        return value

    def install(self, confirm: bool, install_directory: str | None = None, adopt: bool = False, progress: Callable[[str], None] | None = None, version: str | None = None) -> dict[str, Any]:
        """Verifica SHA-256 e --version antes de trocar apenas a referência ativa."""
        if confirm is not True:
            raise TunnelError("confirmation_required", "Instalação de cloudflared exige confirm=true.")
        report = progress or (lambda _value: None)
        settings = self.configuration.settings()
        previous = str(settings.get("cloudflared_executable", "cloudflared"))
        managed = str(settings.get("managed_version", ""))
        if previous != "cloudflared" and not managed and not adopt:
            raise TunnelError("adoption_required", "Executável externo configurado; use adopt=true para selecionar uma instalação gerenciada preservando o arquivo externo.")
        if install_directory is not None and (not isinstance(install_directory, str) or not Path(install_directory).is_absolute()):
            raise TunnelError("invalid_install_directory", "O diretório de instalação deve ser absoluto.")
        base = Path(install_directory or settings.get("install_directory") or self.configuration.path.parent / "bin").resolve()
        if base.is_relative_to(self.configuration.root) and not base.is_relative_to(self.configuration.path.parent.resolve()):
            raise TunnelError("invalid_install_directory", "A instalação dentro da instância deve permanecer no diretório do serviço.")
        report("consultando versão oficial")
        selected_version = version
        release = self._release(selected_version)
        if release.get("draft") or release.get("prerelease"):
            raise TunnelError("invalid_release", "Selecione uma versão oficial estável do cloudflared.")
        version = str(release.get("tag_name", ""))
        if not re.fullmatch(r"v?\d{4}\.\d+\.\d+", version):
            raise TunnelError("invalid_release", "Identificador da versão oficial inválido.")
        if selected_version is not None and version != selected_version:
            raise TunnelError("invalid_release", "A fonte oficial não confirmou a versão selecionada.")
        name = asset_name()
        matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
        if len(matches) != 1:
            raise TunnelError("asset_unavailable", "A versão oficial não oferece o binário desta plataforma.")
        asset = matches[0]
        digest = str(asset.get("digest", ""))
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise TunnelError("release_digest_missing", "A versão oficial não fornece SHA-256 verificável para este binário.")
        url = str(asset.get("browser_download_url", ""))
        expected_url = f"https://github.com/cloudflare/cloudflared/releases/download/{version}/{name}"
        if url != expected_url:
            raise TunnelError("invalid_release", "Origem de download diferente do repositório oficial.")
        destination = base / "versions" / version / ("cloudflared.exe" if name.endswith(".exe") else "cloudflared")
        size = asset.get("size")
        if type(size) is not int or not 0 < size <= 256 * 1024 * 1024:
            raise TunnelError("invalid_release", "O tamanho declarado do binário oficial é inválido.")
        try:
            base.mkdir(parents=True, exist_ok=True)
            if not os.access(base, os.W_OK):
                raise TunnelError("install_permission_denied", "O diretório de instalação não permite gravação.")
            if shutil.disk_usage(base).free < size * 2:
                raise TunnelError("insufficient_space", "Não há espaço suficiente no diretório de instalação.")
        except OSError as error:
            raise TunnelError("install_permission_denied", f"Não foi possível criar ou acessar o diretório de instalação ({_system_error_detail(error)}).") from error
        temporary_root = self.configuration.root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with tempfile.TemporaryDirectory(prefix="cloudflared-install-", dir=temporary_root) as directory:
                staged = Path(directory) / destination.name
                report("baixando binário oficial")
                try:
                    with urllib.request.urlopen(url, timeout=60) as response, staged.open("wb") as output:
                        shutil.copyfileobj(response, output)
                except OSError as error:
                    raise TunnelError("download_failed", f"Não foi possível baixar cloudflared oficial ({_system_error_detail(error)}).") from error
                actual = hashlib.sha256(staged.read_bytes()).hexdigest()
                if staged.stat().st_size != size or actual != digest.split(":", 1)[1].lower():
                    raise TunnelError("checksum_mismatch", "SHA-256 do binário não corresponde à versão oficial.")
                if os.name != "nt":
                    staged.chmod(staged.stat().st_mode | 0o111)
                report("validando executável")
                try:
                    checked = subprocess.run([str(staged), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
                except (OSError, subprocess.SubprocessError) as error:
                    raise TunnelError("invalid_executable", f"Não foi possível validar o executável baixado ({_system_error_detail(error)}).") from error
                if checked.returncode or version.lstrip("v") not in checked.stdout:
                    raise TunnelError("invalid_executable", "O executável baixado não confirmou a versão esperada.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if hashlib.sha256(destination.read_bytes()).hexdigest() != actual:
                        raise TunnelError("version_conflict", "O diretório desta versão contém outro binário; nenhum arquivo foi substituído.")
                else:
                    # O arquivo final é exclusivo: uma falha de cópia deve limpar
                    # também os bytes parciais, sem tocar numa versão preexistente.
                    with destination.open("xb") as output:
                        created = True
                        with staged.open("rb") as source:
                            shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    shutil.copystat(staged, destination)
                report("ativando referência da versão")
                before = {"previous_executable": previous, "previous_version": settings.get("managed_version", ""), "previous_digest": settings.get("managed_digest", "")}
                if previous == str(destination):
                    before = {key: settings.get(key, "") for key in before}
                self.configuration.configure_executable({"cloudflared_executable": str(destination), "install_directory": str(base), "managed_version": version, "managed_digest": digest.lower(), **before})
        except OSError as error:
            if created:
                destination.unlink(missing_ok=True)
            raise TunnelError("install_permission_denied", f"Não foi possível gravar o executável no diretório de instalação ({_system_error_detail(error)}).") from error
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
        return {"version": version, "digest": digest.lower(), "executable": str(destination), "previous_executable": before["previous_executable"]}

    def rollback(self, confirm: bool) -> dict[str, Any]:
        """Restaura a referência anterior após validar sua execução, sem apagar versões."""
        if confirm is not True:
            raise TunnelError("confirmation_required", "Rollback de cloudflared exige confirm=true.")
        settings = self.configuration.settings()
        previous = str(settings.get("previous_executable", ""))
        executable = shutil.which(previous) or previous
        if not previous or not Path(executable).is_file():
            raise TunnelError("rollback_unavailable", "O executável anterior não está disponível para rollback.")
        digest = str(settings.get("previous_digest", ""))
        try:
            if digest and hashlib.sha256(Path(executable).read_bytes()).hexdigest() != digest.removeprefix("sha256:"):
                raise TunnelError("checksum_mismatch", "O executável anterior foi alterado desde a instalação.")
        except OSError as error:
            raise TunnelError("invalid_executable", f"Não foi possível verificar o executável anterior ({_system_error_detail(error)}).") from error
        try:
            checked = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise TunnelError("invalid_executable", f"Não foi possível validar o executável anterior ({_system_error_detail(error)}).") from error
        if checked.returncode or "cloudflared" not in checked.stdout.lower():
            raise TunnelError("invalid_executable", "O executável anterior não confirmou cloudflared.")
        version = str(settings.get("previous_version", ""))
        if version and version.lstrip("v") not in checked.stdout:
            raise TunnelError("invalid_executable", "O executável anterior não confirmou a versão registrada.")
        self.configuration.configure_executable({"cloudflared_executable": executable, "managed_version": version, "managed_digest": digest,
                                      "previous_executable": settings["cloudflared_executable"], "previous_version": settings.get("managed_version", ""),
                                      "previous_digest": settings.get("managed_digest", "")})
        return {"executable": executable, "version": version}
