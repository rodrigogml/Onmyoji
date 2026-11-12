import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from onmyoji_daemon.mcp.mercado.exception import MercadoError
from onmyoji_daemon.mcp.mercado.oauth import MercadoOAuth
from onmyoji_daemon.mcp.oauth_broker import OAUTH_CALLBACKS


class Credentials:
    def __init__(self):
        self.notes = "{}"
        self.fail = False

    def resolve(self, profile, entry, field="password"):
        if field == "username": return "client-id"
        if field == "password": return "client-secret"
        if field == "notes": return self.notes
        raise AssertionError(field)

    def save_oauth_state(self, profile, entry, state):
        if self.fail:
            from onmyoji_daemon.mcp.credentials import CredentialError
            raise CredentialError("vault_write_failed", "falhou")
        self.notes = json.dumps(state)


@pytest.fixture
def oauth(monkeypatch, tmp_path):
    profile = {"enabled": True, "description": "Teste", "modules": ["mercadolivre.account"], "mercadolivre": {"application": "principal", "site_id": "MLB", "auth_mode": "authorization_code", "authorization_credential_profile": "vault", "authorization_entry_path": "auth"}}
    application = {"environment": "production", "credential_profile": "vault", "credential_entry_path": "app", "redirect_uri": "https://gateway.example/oauth/mcp/mercadolivre/callback", "scopes": ["read", "write", "offline_access"]}
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.oauth.load_profile", lambda root, name, provider: (profile, {"timeout_seconds": 1, "max_response_bytes": 10000}, profile[provider]))
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.oauth.load_configuration", lambda root: {"applications": {"mercadolivre": {"principal": application}}})
    credentials = Credentials()
    return MercadoOAuth(tmp_path, "mercadolivre", "pessoal", credentials), credentials


def test_callbacks_mercado_sao_mapeados_sem_fallback():
    assert OAUTH_CALLBACKS["/oauth/mcp/mercadolivre/callback"] == "mercadolivre"
    assert OAUTH_CALLBACKS["/oauth/mcp/mercadopago/callback"] == "mercadopago"
    assert "/oauth/mcp/desconhecido/callback" not in OAUTH_CALLBACKS


def test_inicio_usa_pkce_s256_e_state_sem_segredo(oauth):
    client, credentials = oauth
    result = client.start("state-value-with-enough-entropy", "session", client.application["redirect_uri"])
    query = parse_qs(urlsplit(result["authorizationUrl"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state-value-with-enough-entropy"]
    assert "client_secret" not in query
    pending = json.loads(credentials.notes)["pending"]["session"]
    assert pending["stateHash"] == hashlib.sha256(b"state-value-with-enough-entropy").hexdigest()
    assert "verifier" in pending


def test_cancelamento_preserva_tokens(oauth):
    client, credentials = oauth
    credentials.notes = json.dumps({"tokens": {"refreshToken": "anterior"}, "pending": {"session": {"binding": client.binding()}}})
    client.cancel("session")
    stored = json.loads(credentials.notes)
    assert stored["tokens"] == {"refreshToken": "anterior"}
    assert stored["pending"] == {}


def test_binding_divergente_exige_nova_autorizacao(oauth):
    client, credentials = oauth
    credentials.notes = json.dumps({"tokens": {"accessToken": "a", "refreshToken": "r", "expiresEpoch": 9999999999, "binding": "outro"}})
    with pytest.raises(MercadoError, match="Autorize novamente") as captured:
        client.access()
    assert captured.value.code == "oauth_required"


def test_falha_apos_rotacao_bloqueia_reuso(oauth, monkeypatch):
    client, credentials = oauth
    credentials.notes = json.dumps({"tokens": {"accessToken": "a", "refreshToken": "r", "expiresEpoch": 0, "binding": client.binding(), "userId": "1"}})
    monkeypatch.setattr(client, "_token_request", lambda fields: {"access_token": "novo", "refresh_token": "novo-r", "expires_in": 3600, "user_id": 1})
    credentials.fail = True
    with pytest.raises(MercadoError) as captured:
        client.access()
    assert captured.value.code == "token_rotation_uncertain"
    credentials.fail = False
    with pytest.raises(MercadoError) as repeated:
        client.access()
    assert repeated.value.code == "token_rotation_uncertain"
