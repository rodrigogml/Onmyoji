"""Cliente Cloudflare para um Tunnel e um hostname, sem sobrescrever DNS alheio."""
from __future__ import annotations

import json
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .configuration import TunnelDefinition, validate_origin
from .exception import TunnelError


def _json_type(value: Any) -> str:
    """Descreve somente o tipo JSON; nunca reproduz valores da API."""
    if value is None:
        return "nulo"
    if isinstance(value, bool):
        return "booleano"
    if isinstance(value, dict):
        return "objeto"
    if isinstance(value, list):
        return "lista"
    if isinstance(value, str):
        return "texto"
    if isinstance(value, (int, float)):
        return "número"
    return "tipo desconhecido"


def validated_ingress(config: Any) -> list[dict[str, Any]]:
    """Valida regras remotas sem registrar conteúdos, hostnames ou credenciais."""
    if not isinstance(config, dict):
        raise TunnelError("invalid_ingress", f"Configuração Cloudflare inválida: config é {_json_type(config)} (esperado objeto). Nenhuma alteração remota foi realizada.")
    ingress = config.get("ingress")
    if not isinstance(ingress, list):
        raise TunnelError("invalid_ingress", f"Configuração Cloudflare inválida: config.ingress é {_json_type(ingress)} (esperada lista). Nenhuma alteração remota foi realizada.")
    for index, rule in enumerate(ingress):
        if not isinstance(rule, dict):
            raise TunnelError("invalid_ingress", f"Configuração Cloudflare inválida: config.ingress[{index}] é {_json_type(rule)} (esperado objeto). Nenhuma alteração remota foi realizada.")
        service = rule.get("service")
        if not isinstance(service, str) or not service.strip():
            kind = "ausente" if "service" not in rule else ("texto vazio" if isinstance(service, str) else _json_type(service))
            raise TunnelError("invalid_ingress", f"Configuração Cloudflare inválida: config.ingress[{index}].service é {kind} (esperado texto não vazio). Nenhuma alteração remota foi realizada.")
    return ingress


def gateway_ingress(previous: dict[str, Any], local_url: str, ca_file: str) -> list[dict[str, Any]]:
    """Define TLS da origem local sem herdar SNI/Host de uma publicação antiga."""
    origin = {"caPool": str(ca_file), "noTLSVerify": False}
    inherited = previous.get("originRequest")
    if inherited is None:
        inherited = {}
    if not isinstance(inherited, dict):
        raise TunnelError("invalid_ingress", f"Configuração Cloudflare inválida: config.originRequest é {_json_type(inherited)} (esperado objeto). Nenhuma alteração remota foi realizada.")
    parsed = urllib.parse.urlsplit(local_url)
    if "originServerName" in inherited:
        origin["originServerName"] = parsed.hostname
    if "httpHostHeader" in inherited:
        origin["httpHostHeader"] = parsed.netloc
    return [{"service": local_url, "originRequest": origin}, {"service": "http_status:404"}]



def _safe_endpoint(path: str) -> str:
    """Exibe o recurso sem IDs, hostnames nem query strings."""
    route = urllib.parse.urlsplit(path).path
    route = re.sub(r"(/zones/)[A-Za-z0-9_-]+", r"\1{zone_id}", route, count=1)
    route = re.sub(r"(/accounts/)[A-Za-z0-9_-]+", r"\1{account_id}", route, count=1)
    route = re.sub(r"(/cfd_tunnel/)[A-Za-z0-9_-]+", r"\1{tunnel_id}", route, count=1)
    route = re.sub(r"(/dns_records/)[A-Za-z0-9_-]+", r"\1{record_id}", route, count=1)
    return route if re.fullmatch(r"[A-Za-z0-9/_{}.-]+", route) and route.startswith(("/zones/", "/accounts/", "/user/tokens/")) else "/recurso-cloudflare"


def _error_codes(payload: Any) -> str:
    """Somente códigos numéricos, inclusive error_chain; nunca mensagens externas."""
    if not isinstance(payload, dict) or not isinstance(payload.get("errors"), list):
        return ""
    codes = []
    queue = list(payload["errors"][:8])
    while queue and len(codes) < 32:
        entry = queue.pop(0)
        if not isinstance(entry, dict):
            continue
        value = entry.get("code")
        if type(value) is int and 0 <= value <= 9999999999:
            codes.append(str(value))
        elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
            codes.append(value)
        nested = entry.get("error_chain")
        if isinstance(nested, list):
            queue.extend(nested[:8])
    return ", ".join(dict.fromkeys(codes))


def _http_advice(status: int, endpoint: str) -> str:
    if status == 401:
        return "API Token administrativo rejeitado. Confira o valor no KeePass, expiração e restrições de IP."
    if status == 403:
        if endpoint.startswith("/zones/"):
            return "Acesso DNS negado. Confira Zone ID, zona autorizada e Zone > DNS > Edit no API Token."
        if "/cfd_tunnel" in endpoint:
            return "Acesso ao Tunnel negado. Confira Account ID, conta autorizada e Account > Cloudflare Tunnel > Edit no API Token."
        return "Acesso não autorizado. Confira o API Token, conta selecionada, escopos e restrições de IP."
    if status == 404:
        return "Recurso não encontrado. Confira Account ID, Zone ID e se o Tunnel pertence à conta."
    if status == 429:
        return "Limite temporário de requisições Cloudflare; tente novamente após o intervalo indicado pelo serviço."
    return "Confira o estado da API Cloudflare e os identificadores configurados."


class CloudflareHTTPError(TunnelError):
    """Mantém status HTTP para diagnóstico sem propagar a resposta original."""

    def __init__(self, status: int, method: str, path: str, payload: Any):
        self.http_status = status
        endpoint = _safe_endpoint(path)
        codes = _error_codes(payload)
        self.cloudflare_codes = frozenset(codes.split(", ")) if codes else frozenset()
        suffix = f" Códigos Cloudflare: {codes}." if codes else ""
        super().__init__("cloudflare_http_error", f"Cloudflare HTTP {status} em {method} {endpoint}.{suffix} {_http_advice(status, endpoint)}")


class CloudflareClient:
    def __init__(self, token: str, base_url: str = "https://api.cloudflare.com/client/v4", timeout: float = 30):
        if not token:
            raise TunnelError("credential_missing", "Token Cloudflare não informado.")
        self.token, self.base_url, self.timeout = token, base_url.rstrip("/"), timeout

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=payload, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                remote_error = json.loads(error.read(4096))
            except (AttributeError, OSError, TypeError, ValueError):
                remote_error = None
            raise CloudflareHTTPError(error.code, method, path, remote_error) from error
        except (OSError, ValueError) as error:
            raise TunnelError("cloudflare_unavailable", "Falha de comunicação com a Cloudflare.") from error
        if not isinstance(value, dict) or value.get("success") is not True:
            codes = _error_codes(value)
            detail = f" Códigos Cloudflare: {codes}." if codes else ""
            raise TunnelError("cloudflare_rejected", f"Cloudflare rejeitou {method} {_safe_endpoint(path)}.{detail} Confira permissões e identificadores.")
        return value.get("result")


    def verify_token(self, account_id: str) -> str:
        """Compatível com tokens de usuário e de conta, sem listar tokens."""
        paths = ("/user/tokens/verify", f"/accounts/{account_id}/tokens/verify")
        for path in paths:
            try:
                result = self._call("GET", path)
            except CloudflareHTTPError as error:
                # A recusa em ambos os endpoints não comprova token inválido.
                # A autorização efetiva será testada nos recursos específicos.
                if error.http_status in {401, 403, 404}:
                    continue
                raise
            if not isinstance(result, dict) or result.get("status") not in {"active", "disabled", "expired"}:
                raise TunnelError("invalid_response", "Cloudflare não retornou um estado reconhecido do API Token.")
            if result["status"] != "active":
                raise TunnelError("cloudflare_token_inactive", f"API Token Cloudflare {result['status']}. Reative ou substitua a credencial no KeePass.")
            return "active"
        return "unverified"

    def named_tunnels(self, definition: TunnelDefinition) -> list[dict[str, Any]]:
        definition.validate()
        matches = self._call("GET", f"/accounts/{definition.account_id}/cfd_tunnel?" + urllib.parse.urlencode({"name": definition.tunnel_name, "is_deleted": "false"})) or []
        if not isinstance(matches, list) or any(not isinstance(item, dict) for item in matches):
            raise TunnelError("invalid_response", "Cloudflare não confirmou a consulta de Tunnels da conta.")
        return matches

    def validate_zone_hostname(self, definition: TunnelDefinition) -> None:
        """Recusa Zone ID de outro domínio antes de criar ou modificar o DNS."""
        definition.validate()
        try:
            zone = self._call("GET", f"/zones/{definition.zone_id}")
        except TunnelError as error:
            raise TunnelError(
                "zone_unverified",
                "Não foi possível confirmar a identidade da zona Cloudflare; nenhum DNS foi alterado. "
                "Verifique o Zone ID e as permissões Zone > DNS > Edit do API Token no KeePass.",
            ) from error
        name = zone.get("name") if isinstance(zone, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise TunnelError(
                "zone_unverified",
                "A Cloudflare não retornou o domínio da zona selecionada; nenhum DNS foi alterado. "
                "Confira o Zone ID informado no painel.",
            )
        zone_name = name.strip().rstrip(".").casefold()
        hostname = definition.hostname.rstrip(".").casefold()
        if hostname != zone_name and not hostname.endswith("." + zone_name):
            raise TunnelError(
                "zone_mismatch",
                "O Zone ID configurado pertence a outro domínio e não corresponde ao hostname público. "
                "Abra o domínio do hostname no painel Cloudflare, copie o Zone ID correto "
                "e atualize a opção 1 da Publicação externa. Nenhum DNS foi alterado.",
            )

    def preflight_access(self, definition: TunnelDefinition, progress) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """Testa DNS e Tunnels independentemente, sem alteração remota."""
        definition.validate()
        progress("verificando validade do API Token administrativo na Cloudflare")
        token_status = self.verify_token(definition.account_id)
        progress("confirmando que o Zone ID corresponde ao domínio do hostname")
        self.validate_zone_hostname(definition)
        progress("consultando permissões efetivas de leitura: DNS da zona e Tunnels da conta")
        records, matches = None, None
        failures: list[str] = []
        try:
            records = self.dns_records(definition)
        except TunnelError as error:
            failures.append(f"DNS: FALHOU ({error})")
        try:
            matches = self.named_tunnels(definition)
        except TunnelError as error:
            failures.append(f"Tunnels: FALHOU ({error})")
        if failures:
            dns = "DNS: OK" if records is not None else next(item for item in failures if item.startswith("DNS:"))
            tunnels = "Tunnels: OK" if matches is not None else next(item for item in failures if item.startswith("Tunnels:"))
            verification = "ativo" if token_status == "active" else "não confirmado"
            raise TunnelError("cloudflare_access_failed", f"Pré-verificação Cloudflare: API Token {verification}; {dns}; {tunnels}. Nenhuma alteração remota foi realizada.")
        return records, matches, token_status

    @staticmethod
    def _confirmed(confirm: bool) -> None:
        if confirm is not True:
            raise TunnelError("confirmation_required", "Operações de escrita Cloudflare exigem confirm=true.")

    def create_tunnel(self, definition: TunnelDefinition, confirm: bool) -> dict[str, Any]:
        definition.validate()
        self._confirmed(confirm)
        existing = self.named_tunnels(definition)
        if existing:
            raise TunnelError("tunnel_name_conflict", "Já existe um Tunnel com esse nome; informe seu ID explicitamente para adotá-lo.")
        result = self._call("POST", f"/accounts/{definition.account_id}/cfd_tunnel", {"name": definition.tunnel_name, "config_src": "cloudflare"})
        if not isinstance(result, dict) or not result.get("id"):
            raise TunnelError("invalid_response", "Cloudflare não retornou o ID do Tunnel criado.")
        return result

    def get_configuration(self, definition: TunnelDefinition) -> dict[str, Any]:
        definition.validate()
        result = self._call("GET", f"/accounts/{definition.account_id}/cfd_tunnel/{definition.tunnel_id}/configurations")
        if not isinstance(result, dict):
            raise TunnelError("invalid_response", "Cloudflare não retornou a configuração do Tunnel.")
        # Tunnels ainda sem publicação podem retornar config/ingress ausente ou null.
        # Um fallback 404 é semanticamente vazio e pode ser restaurado via PUT.
        config = result.get("config")
        if config is None:
            config = {}
        if isinstance(config, dict) and (config.get("ingress") is None or config.get("ingress") == []):
            config = {**config, "ingress": [{"service": "http_status:404"}]}
        validated_ingress(config)
        return {**result, "config": config}

    def set_configuration(self, definition: TunnelDefinition, config: dict[str, Any], confirm: bool) -> None:
        definition.validate()
        self._confirmed(confirm)
        if not isinstance(config, dict) or not isinstance(config.get("config"), dict):
            raise TunnelError("invalid_ingress", "Configuração remota de ingress inválida.")
        self._call("PUT", f"/accounts/{definition.account_id}/cfd_tunnel/{definition.tunnel_id}/configurations", {"config": config["config"]})

    def configure_ingress(self, definition: TunnelDefinition, tunnel_id: str, local_url: str, confirm: bool, ca_file: str, *, expected_previous: dict[str, Any] | None = None) -> None:
        """Reutiliza a origem exata ou substitui somente um snapshot autorizado.

        ``ingress_conflict`` indica publicação desconhecida ou alteração remota
        posterior ao diagnóstico; nenhuma regra é sobrescrita nessa situação.
        """
        definition.validate()
        self._confirmed(confirm)
        validate_origin(local_url, ca_file)
        current = self.get_configuration(TunnelDefinition(definition.account_id, definition.zone_id, definition.hostname, tunnel_id, definition.tunnel_name))
        previous = current.get("config", {})
        ingress = validated_ingress(previous)
        named = [rule for rule in ingress if rule.get("hostname")]
        unnamed = [rule for rule in ingress if not rule.get("hostname")]
        if len(named) > 1 or any(rule.get("hostname") != definition.hostname or rule.get("path") for rule in named) or len(unnamed) > 1 or any(rule != {"service": "http_status:404"} for rule in unnamed) or (unnamed and ingress[-1] is not unnamed[0]):
            raise TunnelError("ingress_conflict", "O Tunnel contém publicações adicionais; nenhuma regra foi substituída.")
        desired = gateway_ingress(previous, local_url, ca_file)
        desired[0] = {"hostname": definition.hostname, **desired[0]}
        if expected_previous is not None and (not isinstance(expected_previous, dict) or expected_previous.get("config") != previous):
            raise TunnelError("ingress_conflict", "A configuração remota mudou após o diagnóstico; nenhuma regra foi substituída.")
        if ingress == desired:
            return
        if named and expected_previous is None:
            raise TunnelError("ingress_conflict", "O hostname possui uma origem não confirmada; use a migração explícita para adotá-la.")
        config = {"config": {**previous, "ingress": desired}}
        self.set_configuration(TunnelDefinition(definition.account_id, definition.zone_id, definition.hostname, tunnel_id, definition.tunnel_name), config, True)

    def _dns_records_unfiltered_for_hostname(self, definition: TunnelDefinition) -> tuple[list[dict[str, Any]], bool]:
        """Varredura limitada somente para investigar uma recusa 81053."""
        for page in range(1, 21):
            query = urllib.parse.urlencode({"page": page, "per_page": 100})
            records = self._call("GET", f"/zones/{definition.zone_id}/dns_records?{query}")
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise TunnelError("invalid_response", "Cloudflare não confirmou a listagem DNS para o diagnóstico.")
            found = [record for record in records
                     if isinstance(record.get("name"), str)
                     and record["name"].rstrip(".").casefold() == definition.hostname.rstrip(".").casefold()]
            if found:
                return found, True
            if len(records) < 100:
                return [], True
        return [], False

    def _dns_create_conflict(self, definition: TunnelDefinition) -> TunnelError:
        """Explica uma colisão remota sem reproduzir registros, tokens nem IDs."""
        details: list[str] = []
        try:
            filtered = self.dns_records(definition)
        except TunnelError:
            filtered = None
            details.append("Consulta por hostname: indisponível.")
        else:
            if filtered:
                details.append("Consulta por hostname agora encontra registro(s) DNS do mesmo nome.")
            else:
                details.append("Consulta por hostname continua sem retornar registros.")
        if not filtered:
            try:
                other, complete = self._dns_records_unfiltered_for_hostname(definition)
            except TunnelError:
                details.append("Listagem DNS da zona sem filtro: indisponível.")
            else:
                if other:
                    types = ", ".join(sorted({str(record.get("type")) if record.get("type") in {"A", "AAAA", "CNAME"} else "outro"
                                             for record in other}))
                    details.append(f"Listagem sem filtro encontrou registro(s) do hostname (tipos: {types}), não retornados pelo filtro.")
                elif complete:
                    details.append("Listagem completa da zona não encontrou registro do hostname.")
                else:
                    details.append("Listagem da zona ultrapassou 20 páginas; resultado inconclusivo.")
        try:
            zone = self._call("GET", f"/zones/{definition.zone_id}")
        except TunnelError:
            details.append("Não foi possível consultar o nome da zona (Zone Read pode ser necessário).")
        else:
            zone_name = zone.get("name") if isinstance(zone, dict) else None
            if isinstance(zone_name, str) and zone_name.strip():
                normalized = zone_name.rstrip(".").casefold()
                hostname = definition.hostname.rstrip(".").casefold()
                if hostname == normalized or hostname.endswith("." + normalized):
                    details.append("O Zone ID corresponde ao domínio do hostname.")
                else:
                    details.append("O Zone ID aponta para outro domínio; revise a zona configurada.")
            else:
                details.append("A API não informou o nome da zona para conferência.")
        return TunnelError(
            "dns_conflict",
            "A Cloudflare recusou criar o CNAME (81053: já existe A/AAAA/CNAME com o mesmo nome). "
            + " ".join(details)
            + " Nenhum DNS foi removido ou substituído. Confira registros, zona e eventuais DNS gerenciados na Cloudflare antes de tentar novamente.",
        )

    def _create_dns_record(self, definition: TunnelDefinition, tunnel_id: str) -> dict[str, Any]:
        """Cria somente o CNAME autorizado; em 81053, coleta evidências sem escrever."""
        self.validate_zone_hostname(definition)
        target = f"{tunnel_id}.cfargotunnel.com"
        try:
            result = self._call("POST", f"/zones/{definition.zone_id}/dns_records",
                                {"type": "CNAME", "name": definition.hostname, "content": target, "proxied": True})
        except CloudflareHTTPError as error:
            if error.http_status == 400 and "81053" in error.cloudflare_codes:
                raise self._dns_create_conflict(definition) from error
            raise
        if not isinstance(result, dict) or not result.get("id"):
            raise TunnelError("invalid_response", "Cloudflare não confirmou o ID do CNAME criado; confira a zona antes de repetir a operação.")
        return result

    def inspect_dns(self, definition: TunnelDefinition, tunnel_id: str) -> dict[str, Any] | None:
        """Consulta o hostname sem escrita e rejeita qualquer registro incompatível."""
        definition.validate()
        target = f"{tunnel_id}.cfargotunnel.com"
        records = self.dns_records(definition)
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise TunnelError("invalid_response", "Cloudflare não confirmou a lista de registros DNS.")
        if records:
            if len(records) != 1 or records[0].get("type") != "CNAME" or str(records[0].get("content", "")).lower().rstrip(".") != target.lower() or records[0].get("proxied") is not True:
                raise TunnelError("dns_conflict", "O hostname já possui um registro DNS incompatível com o Tunnel selecionado. Execute o wizard de publicação para consultar a Cloudflare e reutilizar a publicação existente; nenhum registro foi substituído.")
            return dict(records[0])
        return None

    def dns_records(self, definition: TunnelDefinition) -> list[dict[str, Any]]:
        """Lê somente registros do hostname para descoberta sem configuração antiga."""
        definition.validate()
        records = self._call("GET", f"/zones/{definition.zone_id}/dns_records?" + urllib.parse.urlencode({"name": definition.hostname, "per_page": 100})) or []
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise TunnelError("invalid_response", "Cloudflare não confirmou a lista de registros DNS.")
        return records

    def discover_tunnel(self, definition: TunnelDefinition, *, records: list[dict[str, Any]] | None = None, matches: list[dict[str, Any]] | None = None) -> tuple[TunnelDefinition | None, dict[str, Any] | None]:
        """Descobre um Tunnel remoto por DNS, ID ou nome, sem escritas.

        Recusa DNS externo, múltiplos registros e Tunnels locais. Um ID local
        divergente não substitui o candidato do DNS: sua troca exige a prévia e
        a confirmação explícitas do wizard antes de persistir qualquer alteração.
        """
        if records is None:
            records = self.dns_records(definition)
        record = records[0] if len(records) == 1 else None
        tunnel_id = definition.tunnel_id
        if records:
            match = re.fullmatch(r"([A-Za-z0-9_-]+)\.cfargotunnel\.com", str((record or {}).get("content", "")).lower().rstrip("."))
            if not record or record.get("type") != "CNAME" or not match or record.get("proxied") is not True:
                raise TunnelError("dns_conflict", "O hostname possui DNS que não é um CNAME proxy para um Cloudflare Tunnel. Confira os registros na zona Cloudflare; o setup não remove registros A/AAAA/CNAME externos automaticamente.")
            discovered = match.group(1)
            tunnel_id = discovered
        if not tunnel_id:
            if matches is None:
                matches = self.named_tunnels(definition)
            if not matches:
                return None, None
            if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
                raise TunnelError("tunnel_name_conflict", "Mais de um Tunnel corresponde ao nome; configure o ID do Tunnel desejado explicitamente.")
            tunnel_id = matches[0]["id"]
        found = TunnelDefinition(definition.account_id, definition.zone_id, definition.hostname, tunnel_id, definition.tunnel_name)
        found.validate()
        details = self.status(definition.account_id, tunnel_id)
        if details.get("id") != tunnel_id or details.get("deleted_at") or details.get("config_src") != "cloudflare":
            raise TunnelError("unsupported_remote_tunnel", "O Tunnel encontrado não está ativo como configuração administrada na Cloudflare. Confira a conta e converta Tunnels locais antes de reutilizá-los pelo setup.")
        name = details.get("name")
        found = TunnelDefinition(found.account_id, found.zone_id, found.hostname, found.tunnel_id, name if isinstance(name, str) and name.strip() else found.tunnel_name)
        found.validate()
        return found, record

    def ensure_dns(self, definition: TunnelDefinition, tunnel_id: str, confirm: bool) -> dict[str, Any]:
        """Reutiliza o CNAME compatível ou cria um novo, sem sobrescrever registros."""
        self._confirmed(confirm)
        existing = self.inspect_dns(definition, tunnel_id)
        if existing is not None:
            self.validate_zone_hostname(definition)
            return existing
        return self._create_dns_record(definition, tunnel_id)

    def delete_managed_dns(self, definition: TunnelDefinition, tunnel_id: str, record_id: str, confirm: bool) -> None:
        """Remove somente o registro cujo ID e destino ainda comprovem a propriedade."""
        self._confirmed(confirm)
        if not isinstance(record_id, str) or not record_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in record_id):
            raise TunnelError("invalid_dns_record", "Identificador do registro DNS inválido.")
        existing = self.inspect_dns(definition, tunnel_id)
        if existing is None:
            return
        if existing.get("id") != record_id:
            raise TunnelError("dns_conflict", "O registro DNS mudou de proprietário; nenhuma remoção foi feita.")
        self._call("DELETE", f"/zones/{definition.zone_id}/dns_records/{record_id}")

    def status(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        return dict(self._call("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}"))

    def tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        value = self._call("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")
        if not isinstance(value, str) or not value:
            raise TunnelError("invalid_response", "Cloudflare não retornou o token de execução do Tunnel.")
        return value
