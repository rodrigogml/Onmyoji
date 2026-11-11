import asyncio
import os
from pathlib import Path
import sys

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration


def test_stdio_publishes_union_and_structured_module_error(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["test"] = {"cli_command": ["missing"], "database": {"windows": "missing.kdbx", "linux": "/missing.kdbx"}}
    credentials["profiles"]["test"] = {"vault": "test", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "missing"}, "linux": {"mode": "command", "command": ["false"]}}}
    config.save_credentials(credentials)
    document = config.mercado()
    for provider in ("mercadolivre", "mercadopago"):
        document["applications"][provider]["principal"] = {"environment": "production", "credential_profile": "test", "credential_entry_path": f"Applications/{provider}", "redirect_uri": f"https://gateway.example/oauth/mcp/{provider}/callback", "scopes": ["read"]}
    document["profiles"] = {
        "compras": {"enabled": True, "description": "Compras", "modules": ["mercadolivre.purchases"], "mercadolivre": {"application": "principal", "site_id": "MLB", "auth_mode": "authorization_code", "authorization_credential_profile": "test", "authorization_entry_path": "Authorizations/ml"}},
        "recebimentos": {"enabled": True, "description": "Recebimentos", "modules": ["mercadopago.payments"], "mercadopago": {"application": "principal", "site_id": "MLB", "auth_mode": "client_credentials"}},
    }
    config.save_mercado(document)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path)], env=environment)

    async def check():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert {"mercado.list_profiles", "mercadolivre.purchases_list", "mercadopago.payment_get"} <= tools.keys()
                assert tools["mercadopago.payment_get"].input_schema["properties"]["profile"]["enum"] == ["compras", "recebimentos"]
                response = await session.call_tool("mercadopago.payment_get", {"profile": "compras", "payment_id": "1"})
                assert response.is_error is True
                assert response.structured_content["error"] == {"code": "profile_module_unavailable", "message": "O perfil informado não possui integração com este método.", "profile": "compras", "module": "mercadopago.payments", "tool": "mercadopago.payment_get"}

    asyncio.run(check())
