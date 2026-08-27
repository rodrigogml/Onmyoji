import base64
import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class ForwardEmailError(Exception):
    pass


def load_settings(path, profile_name):
    try: raw=tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,tomllib.TOMLDecodeError) as exc: raise ForwardEmailError("perfil inválido") from exc
    c,v=raw.get("defaults",{}),raw.get("profiles",{}).get(profile_name,{})
    if not isinstance(c,dict) or not isinstance(v,dict) or not v.get("vault_profile") or not v.get("vault_entry_path"): raise ForwardEmailError("perfil inválido")
    base = c.get("api_base", "").strip().rstrip("/")
    if not base.startswith("https://") or urllib.parse.urlparse(base).netloc == "": raise ForwardEmailError("forward_email.api_base deve usar HTTPS")
    try: timeout, retries = float(c.get("timeout_seconds", "30")), int(c.get("max_retries", "2"))
    except ValueError as exc: raise ForwardEmailError("timeout_seconds e max_retries devem ser numéricos") from exc
    if timeout <= 0 or retries < 0: raise ForwardEmailError("timeout_seconds deve ser positivo e max_retries não pode ser negativo")
    return {"base":base,"domain":str(v.get("domain","")).strip(),"timeout":timeout,"retries":retries,"vault":{"profile":v["vault_profile"],"entry_path":v["vault_entry_path"],"field":v.get("vault_field","password")},"config_path":path}


def read_token(settings):
    v = settings["vault"]
    request = {"operation":"read","path":v["entry_path"],"field":v.get("field","password"),"auth":{"mode":"configured"}}
    try:
        wrapper=Path(__file__).resolve().parents[2]/"keepass-vault"/"scripts"/"keepass_vault.py";result=subprocess.run([sys.executable,str(wrapper),"--config",str(Path(settings["config_path"]).parent/"keepass.toml"),"--profile",v["profile"]],input=json.dumps(request),text=True,capture_output=True,timeout=settings["timeout"])
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc: raise ForwardEmailError("falha ao consultar o KeePassVault") from exc
    value = payload.get("result",{}).get("value") if payload.get("ok") else None
    if not value: raise ForwardEmailError("KeePassVault não retornou o token da API")
    return value


class Client:
    ALLOWED = {"domains.list", "domains.get", "domains.create", "domains.update", "domains.verify_records", "domains.verify_smtp", "aliases.list", "aliases.get", "aliases.create", "aliases.update", "aliases.delete", "aliases.generate_password"}
    WRITE = {"domains.create", "domains.update", "aliases.create", "aliases.update", "aliases.delete", "aliases.generate_password"}

    def __init__(self, settings, token): self.settings, self.token = settings, token

    def request(self, operation, params, body):
        if operation not in self.ALLOWED: raise ForwardEmailError("operação não suportada")
        domain = params.get("domain") or self.settings["domain"]
        if operation != "domains.list" and not domain: raise ForwardEmailError("domain é obrigatório no perfil ou em params")
        path, method, payload = self._route(operation, params, body, domain)
        return self._send(method, path, payload, params.get("query", {}))

    def _route(self, op, params, body, domain):
        alias = params.get("alias_id") or params.get("alias")
        if op == "domains.list": return "/domains", "GET", None
        if op == "domains.get": return f"/domains/{urllib.parse.quote(domain, safe='')}", "GET", None
        if op == "domains.create": return "/domains", "POST", body
        if op == "domains.update": return f"/domains/{urllib.parse.quote(domain, safe='')}", "PUT", body
        if op in {"domains.verify_records", "domains.verify_smtp"}:
            suffix = "verify-records" if op.endswith("records") else "verify-smtp"
            return f"/domains/{urllib.parse.quote(domain, safe='')}/{suffix}", "GET", None
        if op == "aliases.list": return f"/domains/{urllib.parse.quote(domain, safe='')}/aliases", "GET", None
        if not alias: raise ForwardEmailError("alias_id ou alias é obrigatório")
        base = f"/domains/{urllib.parse.quote(domain, safe='')}/aliases/{urllib.parse.quote(str(alias), safe='')}"
        if op == "aliases.get": return base, "GET", None
        if op == "aliases.create": return f"/domains/{urllib.parse.quote(domain, safe='')}/aliases", "POST", body
        if op == "aliases.update": return base, "PUT", body
        if op == "aliases.delete": return base, "DELETE", None
        if op == "aliases.generate_password": return base + "/generate-password", "POST", body
        raise ForwardEmailError("operação não suportada")

    def _send(self, method, path, body, query):
        query_string = urllib.parse.urlencode(query, doseq=True)
        url = self.settings["base"] + path + (("?" + query_string) if query_string else "")
        auth = base64.b64encode((self.token + ":").encode("utf-8")).decode("ascii")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Basic " + auth, "Accept": "application/json", "Content-Type": "application/json"})
        for attempt in range(self.settings["retries"] + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.settings["timeout"]) as response: return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.settings["retries"]: time.sleep(2 ** attempt); continue
                raise ForwardEmailError(f"Forward Email HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self.settings["retries"]: time.sleep(2 ** attempt); continue
                raise ForwardEmailError("falha de comunicação com Forward Email") from exc


def main(argv=None):
    try:
        args = argv or sys.argv[1:]
        if len(args)!=4 or args[0]!="--config" or args[2]!="--profile": raise ForwardEmailError("uso: forward_email.py --config forward-email.toml --profile nome")
        settings, request = load_settings(args[1],args[3]), json.load(sys.stdin)
        if request.get("version") != 1: raise ForwardEmailError("version deve ser 1")
        operation = request.get("operation")
        if operation in Client.WRITE and request.get("confirm") is not True: raise ForwardEmailError("confirm: true é obrigatório para escrita")
        if operation == "aliases.generate_password" and request.get("body", {}).get("is_override") is True and request.get("confirm") is not True: raise ForwardEmailError("sobrescrita de senha exige confirm: true")
        result = Client(settings, read_token(settings)).request(operation, request.get("params", {}), request.get("body"))
        print(json.dumps({"version": 1, "ok": True, "operation": operation, "data": result}, ensure_ascii=False)); return 0
    except (ForwardEmailError, json.JSONDecodeError) as exc:
        print(json.dumps({"version": 1, "ok": False, "error": {"code": "invalid_request", "message": str(exc)}}, ensure_ascii=False)); return 1


if __name__ == "__main__": sys.exit(main())
