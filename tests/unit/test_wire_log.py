"""The wire log (``pykiro.wire`` at DEBUG): MCP server definitions never reach it, only their names and types."""

from __future__ import annotations

import copy
import json

from pykiro._acp import _redacted

HTTP = {
    "type": "http",
    "name": "app",
    "url": "http://127.0.0.1:5000/mcp/SECRET-PATH",
    "headers": [{"name": "Authorization", "value": "Bearer SECRET-TOKEN"}],
    "_meta": {"vendor": {"apiKey": "SECRET-EXTENSION"}},
}
STDIO = {
    "name": "db",
    "command": "db-mcp",
    "args": ["--password", "SECRET-ARG"],
    "env": [{"name": "DB_PASSWORD", "value": "SECRET-ENV"}],
}


def test_mcp_server_definitions_are_reduced_to_name_and_type():
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "session/new",
        "params": {"cwd": "/work", "mcpServers": [HTTP, STDIO]},
    }
    original = copy.deepcopy(message)

    logged = _redacted(message)

    assert "SECRET" not in json.dumps(logged)
    assert logged["params"]["mcpServers"] == [{"name": "app", "type": "http"}, {"name": "db"}]
    assert logged["params"]["cwd"] == "/work"
    assert message == original  # the message sent to Kiro is untouched


def test_messages_without_mcp_servers_are_logged_as_they_are():
    message = {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "plan"}}}

    assert _redacted(message) is message
