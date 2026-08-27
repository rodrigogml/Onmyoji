import base64
import json
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class CloudflareError(Exception):
    pass


def _json_error(code, message):
    return {"version": 1, "ok": False, "error": {"code": code, "message": message}}


def load_settings(path, profile_name):
    try: raw=tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,tomllib.TOMLDecodeError) as exc: raise CloudflareError("perfil inválido") from exc
    c, v = raw.get("defaults",{}), raw.get("profiles",{}).get(profile_name,{})
    if not isinstance(c,dict) or not isinstance(v,dict) or not v.get("vault_profile") or not v.get("vault_entry_path"): raise CloudflareError("perfil inválido")
    base = c.get("api_base", "").strip().rstrip("/")
    if not base.startswith("https://") or urllib.parse.urlparse(base).netloc == "":
        raise CloudflareError("cloudflare.api_base deve usar HTTPS")
    try:
        timeout = float(c.get("timeout_seconds", "30"))
        retries = int(c.get("max_retries", "2"))
    except ValueError as exc:
        raise CloudflareError("timeout_seconds e max_retries devem ser numéricos") from exc
    if timeout <= 0 or retries < 0:
        raise CloudflareError("timeout_seconds deve ser positivo e max_retries não pode ser negativo")
    return {"base": base, "zone_id": str(v.get("zone_id", "")).strip(), "timeout": timeout, "retries": retries, "vault": {"profile":v["vault_profile"],"entry_path":v["vault_entry_path"],"field":v.get("vault_field","password")},"config_path":path}


def read_token(settings):
    v = settings["vault"]
    request = {"operation": "read", "path": v["entry_path"], "field": v.get("field", "password"), "auth": {"mode":"configured"}}
    try:
        wrapper=Path(__file__).resolve().parents[2]/"keepass-vault"/"scripts"/"keepass_vault.py"; result = subprocess.run([sys.executable,str(wrapper),"--config",str(Path(settings["config_path"]).parent/"keepass.toml"),"--profile",v["profile"]], input=json.dumps(request), text=True, capture_output=True, timeout=settings["timeout"])
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise CloudflareError("falha ao consultar o KeePassVault") from exc
    value = payload.get("result", {}).get("value") if payload.get("ok") else None
    if not value:
        raise CloudflareError("KeePassVault não retornou o API Token")
    return value


class Client:
    WRITE = {"dns.records.create", "dns.records.update", "dns.records.replace", "dns.records.delete", "dns.records.import"}
    ALLOWED = {"zones.list", "zones.get", "dns.records.list", "dns.records.get", "dns.records.create", "dns.records.update", "dns.records.replace", "dns.records.delete", "dns.records.export", "dns.records.import", "dns.usage"}

    def __init__(self, settings, token):
        self.settings, self.token = settings, token

    def request(self, operation, params, body):
        if operation not in self.ALLOWED:
            raise CloudflareError("operação não suportada")
        zone_id = params.get("zone_id") or self.settings["zone_id"]
        if operation != "zones.list" and not zone_id:
            raise CloudflareError("zone_id é obrigatório no perfil ou em params")
        self.settings["query"] = params.get("query", {})
        path, method, payload = self._route(operation, params, body, zone_id)
        return self._send(method, path, payload)

    def _route(self, op, params, body, zone_id):
        record_id = params.get("record_id")
        if op == "zones.list": return "/zones", "GET", None
        if op == "zones.get": return f"/zones/{zone_id}", "GET", None
        if op == "dns.usage": return f"/zones/{zone_id}/dns_records/usage", "GET", None
        if op == "dns.records.export": return f"/zones/{zone_id}/dns_records/export", "GET", None
        if op == "dns.records.list": return f"/zones/{zone_id}/dns_records", "GET", None
        if op == "dns.records.get":
            if not record_id: raise CloudflareError("record_id é obrigatório")
            return f"/zones/{zone_id}/dns_records/{record_id}", "GET", None
        if op == "dns.records.create": return f"/zones/{zone_id}/dns_records", "POST", body
        if op == "dns.records.import": return f"/zones/{zone_id}/dns_records/import", "POST", body
        if op in {"dns.records.update", "dns.records.replace", "dns.records.delete"}:
            if not record_id: raise CloudflareError("record_id é obrigatório")
            method = {"dns.records.update": "PATCH", "dns.records.replace": "PUT", "dns.records.delete": "DELETE"}[op]
            return f"/zones/{zone_id}/dns_records/{record_id}", method, body
        raise CloudflareError("operação não suportada")

    def _send(self, method, path, body):
        query = urllib.parse.urlencode(self.settings.get("query", {}), doseq=True)
        url = self.settings["base"] + path + (("?" + query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", "Content-Type": "application/json"})
        for attempt in range(self.settings["retries"] + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.settings["timeout"]) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not payload.get("success", False): raise CloudflareError("Cloudflare rejeitou a operação")
                return payload.get("result")
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.settings["retries"]:
                    time.sleep(2 ** attempt); continue
                raise CloudflareError(f"Cloudflare HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self.settings["retries"]:
                    time.sleep(2 ** attempt); continue
                raise CloudflareError("falha de comunicação com Cloudflare") from exc


def main(argv=None):
    try:
        args = argv or sys.argv[1:]
        if len(args) != 4 or args[0] != "--config" or args[2] != "--profile": raise CloudflareError("uso: cloudflare.py --config cloudflare.toml --profile nome")
        settings = load_settings(args[1],args[3]); request = json.load(sys.stdin)
        if request.get("version") != 1: raise CloudflareError("version deve ser 1")
        operation = request.get("operation")
        if operation in Client.WRITE and request.get("confirm") is not True: raise CloudflareError("confirm: true é obrigatório para escrita")
        token = read_token(settings)
        result = Client(settings, token).request(operation, request.get("params", {}), request.get("body"))
        print(json.dumps({"version": 1, "ok": True, "operation": operation, "data": result}, ensure_ascii=False))
        return 0
    except (CloudflareError, json.JSONDecodeError) as exc:
        print(json.dumps(_json_error("invalid_request", str(exc)), ensure_ascii=False)); return 1


if __name__ == "__main__": sys.exit(main())
