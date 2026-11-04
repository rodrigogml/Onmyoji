"""Diagnóstico de publicação sem iniciar serviços ou criar configuração local."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tomllib
from typing import Any, Callable

from .mcp.credentials import CredentialError, KeePassCredentialResolver
from .rpc import call
from .runtime import python as runtime_python
from .supervisor import endpoint


def _detail(error: Exception, token: str = "") -> str:
    """Remove dados de autenticação de erros operacionais antes da apresentação."""
    value = str(error).replace("\n", " ")
    if token:
        value = value.replace(token, "[redacted]")
    value = re.sub(r"(?i)(token|password|secret)\s*[=:]\s*\S+", r"\1=[redacted]", value)
    value = re.sub(r"https?://\S+", "[URL omitida]", value)
    return value[-240:] or type(error).__name__


def diagnose(root: Path, progress: Callable[[str], None] | None = None) -> list[dict[str, str]]:
    """Retorna pendências independentes com o próximo passo, sem escritas ou provisionamento.

    O cofre é consultado somente para confirmar a presença da senha. A consulta
    não obtém o token Cloudflare nem comprova suas permissões na API remota.
    Mini Apps é um consumidor opcional e sua ausência não bloqueia o transporte.
    """
    root = root.resolve()
    checks: list[dict[str, str]] = []
    services_dir = root / "configs" / "daemon" / "services"

    def add(state: str, label: str, detail: str, action: str = "") -> None:
        checks.append({"state": state, "label": label, "detail": detail, "action": action})

    def step(message: str) -> None:
        if progress:
            progress(message)

    def read_toml(path: Path, label: str) -> dict[str, Any]:
        try:
            values = tomllib.loads(path.read_text(encoding="utf-8"))
            if type(values.get("schema_version")) is not int or values["schema_version"] != 1:
                raise ValueError("schema_version deve ser 1")
            return values
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            add("error", label, f"Arquivo inválido: {path}. {_detail(error)}", "Revise esse arquivo ou execute W. Wizard de publicação externa.")
            return {}

    step("Verificando o runtime Python e as bibliotecas do Gateway.")
    try:
        executable = runtime_python(root)
        if not executable.is_file():
            add("pending", "Runtime Python", f"O interpretador selecionado não existe: {executable}.", "Use Daemon > Python e bibliotecas para instalar ou reparar os componentes do daemon.")
        else:
            program = "import importlib.metadata as m,json,sys; r={'python':list(sys.version_info[:3])};\nfor n in ('aiohttp','cryptography'):\n try: r[n]=m.version(n)\n except m.PackageNotFoundError: r[n]=None\nprint(json.dumps(r))"
            result = subprocess.run([str(executable), "-B", "-c", program], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
            values = json.loads(result.stdout) if result.returncode == 0 else {}
            missing = [name for name in ("aiohttp", "cryptography") if not values.get(name)]
            valid = bool(values) and tuple(values.get("python", ())) >= (3, 11) and not missing
            add("ok" if valid else "error", "Runtime Python", f"Interpretador: {executable}. " + ("aiohttp e cryptography disponíveis." if valid else "Bibliotecas ausentes/inacessíveis ou Python anterior a 3.11: " + ", ".join(missing)), "" if valid else "Use Python e bibliotecas > Instalar ou reparar componentes do daemon.")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        add("error", "Runtime Python", "A consulta ao interpretador falhou: " + _detail(error), "Repare o runtime em Python e bibliotecas e execute o diagnóstico novamente.")

    step("Consultando o Daemon e seus serviços sem iniciá-los.")
    address: tuple[str, int, str] | None = None
    service_states: dict[str, dict[str, Any]] = {}
    try:
        address = endpoint(root)
        if address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("O endpoint administrativo deve usar loopback local.")
        listed = call(*address, "list-services", {}, timeout=3)
        if not isinstance(listed, list) or any(not isinstance(item, dict) for item in listed):
            raise ValueError("O Daemon retornou uma lista de serviços inválida.")
        service_states = {str(item.get("name")): item for item in listed}
        missing_services = [name for name in ("gateway", "cloudflare-tunnel") if name not in service_states]
        add("pending" if missing_services else "ok", "Daemon e registro dos serviços", "O Daemon ativo ainda não reconhece: " + ", ".join(missing_services) + "." if missing_services else "Daemon acessível; Gateway e Cloudflare Tunnel registrados.", "Reinicie o serviço/processo do Daemon para carregar a versão atualizada." if missing_services else "")
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        detail = "O Daemon não está iniciado; não existe endpoint de controle." if isinstance(error, FileNotFoundError) else "O Daemon não respondeu: " + _detail(error, address[2] if address else "")
        add("pending", "Daemon e registro dos serviços", detail, "Use Daemon > Processo/Serviço do Daemon para iniciar ou reiniciar a instância.")
        address = None

    live: dict[str, dict[str, Any]] = {}
    for name, label in (("gateway", "Gateway HTTP"), ("cloudflare-tunnel", "Serviço Cloudflare Tunnel")):
        state = service_states.get(name, {})
        if not state or state.get("state") != "running":
            add("pending", label, "Serviço " + str(state.get("state") or "não registrado") + ".", "Use Publicação externa > Administrar serviços para iniciar " + name + ".")
            continue
        try:
            status = call(*address, name + ".status", {}, timeout=3)
            if not isinstance(status, dict):
                raise ValueError("Resposta de estado inválida.")
            live[name] = status
            add("ok", label, "Serviço administrativo em execução.")
        except (OSError, ValueError, RuntimeError) as error:
            add("error", label, "Falha na consulta de estado: " + _detail(error, address[2]), "Reinicie esse serviço e consulte seus logs no diagnóstico do Daemon.")

    step("Verificando configuração, identificadores e hostname Cloudflare.")
    settings = read_toml(services_dir / "cloudflare-tunnel" / "tunnel.toml", "Arquivo de configuração do Tunnel")
    fields = (("account_id", "Cloudflare Account ID"), ("zone_id", "Cloudflare Zone ID"), ("hostname", "Hostname público"))
    for key, label in fields:
        present = isinstance(settings.get(key), str) and bool(settings[key].strip())
        add("ok" if present else "pending", label, "Informado na configuração local." if present else "Ainda não informado.", "" if present else "Use W. Wizard ou 1. Configurar publicação externa. Account ID e Zone ID identificam conta/domínio; não são tokens.")
    if all(isinstance(settings.get(key), str) and settings[key].strip() for key, _label in fields):
        from .cloudflare_tunnel.configuration import TunnelDefinition
        from .cloudflare_tunnel.exception import TunnelError
        try:
            TunnelDefinition(*(settings.get(key, "") for key in ("account_id", "zone_id", "hostname", "tunnel_id")), settings.get("tunnel_name", "onmyoji-gateway")).validate()
            add("ok", "Formato da configuração Cloudflare", "Identificadores e hostname possuem formato aceito.")
        except (TunnelError, TypeError) as error:
            add("error", "Formato da configuração Cloudflare", _detail(error), "Revise os identificadores e use um hostname DNS completo, sem https://, porta ou path.")
    legacy = services_dir / "mini-apps" / "tunnel.toml"
    if legacy.is_file():
        add("pending" if not settings.get("hostname") else "ok", "Publicação legada", "Existe configuração do Tunnel no diretório antigo de Mini Apps.", "Use 8. Migrar publicação existente para preservar o Tunnel e o hostname antigos.")

    step("Verificando perfil KeePass e a presença da credencial, sem obter o token.")
    profile, entry = settings.get("credential_profile"), settings.get("credential_entry_path")
    if not isinstance(profile, str) or not profile.strip() or not isinstance(entry, str) or not entry.strip():
        add("pending", "Credencial Cloudflare no KeePass", "Perfil de vault e/ou entrada do API Token ainda não configurados.", "Use W. Wizard ou 1. Configurar publicação externa para selecionar o vault e salvar/reutilizar o token.")
    else:
        try:
            available = KeePassCredentialResolver(root).password_exists(profile, entry)
            add("ok" if available else "error", "Credencial Cloudflare no KeePass", "A senha protegida existe na entrada selecionada. Permissões remotas ainda dependem do provisionamento." if available else "A entrada selecionada não contém senha/API Token.", "" if available else "Execute o wizard e informe o token no campo protegido da entrada KeePass.")
        except CredentialError as error:
            reasons = {"entry_denied": "A entrada está fora das raízes autorizadas do perfil.", "profile_not_found": "O perfil de vault não foi encontrado.", "config_not_found": "A configuração do vault MCP não existe."}
            add("error", "Credencial Cloudflare no KeePass", reasons.get(error.code, "Não foi possível acessar a entrada KeePass.") + f" Código: {error.code}.", "Confira o perfil em MCP > KeePass Vault, desbloqueio e permissões da entrada; depois execute o wizard.")

    step("Verificando o executável cloudflared, sem instalar ou conectar.")
    configured_binary = str(settings.get("cloudflared_executable") or "cloudflared")
    binary = shutil.which(configured_binary) or (configured_binary if Path(configured_binary).is_file() else None)
    if binary is None:
        add("pending", "Executável cloudflared", "Executável ainda não encontrado.", "Use 3. Instalar ou atualizar cloudflared; a instalação não exige IDs ou token Cloudflare.")
    else:
        try:
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
            valid = result.returncode == 0 and "cloudflared" in result.stdout.lower()
            add("ok" if valid else "error", "Executável cloudflared", "Executável respondeu a --version." if valid else f"A validação do executável retornou código {result.returncode} ou uma resposta inesperada.", "" if valid else "Use 3. Instalar ou atualizar cloudflared, ou corrija o executável configurado.")
        except (OSError, subprocess.SubprocessError) as error:
            add("error", "Executável cloudflared", "Não foi possível executar --version: " + _detail(error), "Confira execução/permissões ou reinstale pela opção 3.")

    step("Verificando origem HTTPS, certificados e conexão do Tunnel.")
    gateway_config = read_toml(services_dir / "gateway" / "config.toml", "Arquivo de configuração do Gateway")
    port = gateway_config.get("http_port", 39390)
    gateway = live.get("gateway", {})
    if type(port) is not int or not 1 <= port <= 65535:
        add("error", "Porta HTTPS do Gateway", "A porta configurada não está entre 1 e 65535.", "Corrija em 7. Porta HTTPS do Gateway e reinicie o serviço Gateway.")
    else:
        add("ok", "Porta HTTPS do Gateway", f"Porta configurada: {port}" + (" (padrão)." if not gateway_config else "."))
    ready = gateway.get("ready") is True
    add("ok" if ready else "pending", "Listener HTTPS do Gateway", "O Gateway confirmou seu listener HTTPS local." if ready else "A prontidão HTTPS ainda não foi confirmada.", "" if ready else "Inicie o Gateway em Administrar serviços; verifique porta livre e dependências do daemon.")
    ca = services_dir / "gateway" / "tls" / "ca.pem"
    tls_material = all((ca.parent / name).is_file() for name in ("ca.pem", "gateway.pem", "gateway-key.pem"))
    add("ok" if tls_material else "pending", "Certificado e chave HTTPS do Gateway", "Material TLS local disponível." if tls_material else "Certificado, chave ou CA local ainda ausentes.", "" if tls_material else "Inicie o serviço Gateway para gerar seu material TLS; confira as permissões de configs/daemon/services/gateway/tls/ se a inicialização falhar.")
    if not ca.is_file():
        add("pending", "CA privada do Gateway", "Os certificados locais ainda não foram gerados.", "Inicie o serviço Gateway; ele prepara os certificados HTTPS automaticamente.")
    else:
        try:
            ssl.create_default_context(cafile=str(ca))
            add("ok", "CA privada do Gateway", "Arquivo da CA local legível e válido para TLS.")
        except (OSError, ValueError) as error:
            add("error", "CA privada do Gateway", "Não foi possível carregar a CA local: " + _detail(error), "Confira os certificados em configs/daemon/services/gateway/tls/ antes de reiniciar o Gateway.")
    origin = str(settings.get("gateway_url") or "")
    if origin:
        from .cloudflare_tunnel.configuration import validate_origin
        from .cloudflare_tunnel.exception import TunnelError
        try:
            validate_origin(origin, str(settings.get("ca_file") or ""), require_ca=False)
            expected = str(gateway.get("http_url") or (f"https://localhost:{port}" if type(port) is int else ""))
            matches = origin.rstrip("/") == expected.rstrip("/")
            add("ok" if matches else "pending", "Origem local do Tunnel", "Origem compatível com o Gateway selecionado." if matches else "A origem configurada difere da origem HTTPS atual do Gateway.", "" if matches else "Inicie Gateway e Tunnel para sincronizar a origem antes de provisionar.")
        except TunnelError as error:
            add("error", "Origem local do Tunnel", _detail(error), "Use o wizard para configurar uma origem HTTPS de loopback válida.")
    tunnel = live.get("cloudflare-tunnel", {})
    provisioned = bool(settings.get("tunnel_id") and settings.get("provisioned_gateway_url") == settings.get("gateway_url") and settings.get("provisioned_ca_file") and settings.get("provisioned_ca_file") == settings.get("ca_file"))
    add("ok" if provisioned else "pending", "Tunnel, ingress e DNS provisionados", "Há confirmação local do provisionamento para a origem configurada; não foi feita nova consulta remota." if provisioned else "Ainda não há confirmação de provisionamento para a origem atual.", "" if provisioned else "Conclua o wizard ou use 4. Provisionar Tunnel, ingress e DNS, após configurar credencial, hostname, Gateway e executável.")
    connected = tunnel.get("running") is True and tunnel.get("connected") is True and tunnel.get("ingress_ready") is True
    add("ok" if connected else "pending", "Conexão Cloudflare", "O conector confirmou conexão e origem provisionada." if connected else "Conexão externa ainda não confirmada.", "" if connected else "Inicie o Cloudflare Tunnel e consulte o resultado do provisionamento; confira rede e permissões do API Token.")

    step("Consultando Mini Apps como consumidor opcional.")
    miniapps = service_states.get("mini-apps", {})
    if miniapps.get("state") != "running":
        add("ok", "Mini Apps (opcional)", "Execução local não confirmada; Mini Apps não é requisito para configurar Gateway e Tunnel.", "Para publicar aplicações ou OAuth, use Daemon > MiniApps > Wizard.")
    else:
        try:
            value = call(*address, "mini-apps.diagnose", {}, timeout=3)
            ready = isinstance(value, dict) and value.get("local_ready") is True
            add("ok" if ready else "pending", "Mini Apps (opcional)", "Mini Apps confirmou prontidão local." if ready else "Serviço ativo, com pendências próprias de publicação local.", "" if ready else "Use Daemon > MiniApps > Diagnóstico para conferir workspace, HTTPS e aplicações.")
        except (OSError, ValueError, RuntimeError) as error:
            add("pending", "Mini Apps (opcional)", "Consulta indisponível: " + _detail(error, address[2]), "Use o diagnóstico de Mini Apps; o Tunnel é administrado de forma independente.")
    return checks
