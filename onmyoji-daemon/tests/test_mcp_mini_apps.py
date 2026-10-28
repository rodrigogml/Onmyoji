from unittest.mock import patch

import pytest

from onmyoji_daemon.mcp.mini_apps.service import MiniAppsMcpError
from onmyoji_daemon.mcp.mini_apps.tools import MiniAppsToolRegistry


def test_named_mini_apps_tools_replace_generic_invocation(tmp_path):
    registry = MiniAppsToolRegistry(tmp_path)
    names = {item["name"] for item in registry.definitions()}
    generic_invocation = ".".join(("mini_apps", "invoke"))
    assert generic_invocation not in names
    assert {"mini_apps.status", "mini_apps.create", "mini_apps.publish", "mini_apps.tunnel_provision", "mini_apps.oauth_start"} <= names


def test_mutations_require_confirmation_and_delegate_the_named_action(tmp_path):
    registry = MiniAppsToolRegistry(tmp_path)
    with pytest.raises(MiniAppsMcpError, match="confirm=true"):
        registry.call("mini_apps.publish", {"id": "app-1"})
    with patch("onmyoji_daemon.mcp.mini_apps.tools.MiniAppsMcpService") as service:
        service.return_value.invoke.return_value = {"published": True}
        assert registry.call("mini_apps.publish", {"id": "app-1", "confirm": True}) == {"published": True}
        service.return_value.invoke.assert_called_once_with("publish", {"id": "app-1", "confirm": True})
