"""ACP initialization: require the protocol and capabilities used by pyKiro."""

from __future__ import annotations

import pytest

from pykiro import KiroError
from pykiro._acp import _validate_initialize


def initialized() -> dict:
    return {
        "protocolVersion": 1,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": True, "embeddedContext": True},
            "mcpCapabilities": {"http": True},
            "sessionCapabilities": {
                "list": {},
                "delete": {},
                "additionalDirectories": {},
                "close": {},
            },
        },
    }


def test_initialize_accepts_the_acp_contract_pykiro_uses():
    _validate_initialize(initialized())


@pytest.mark.parametrize("response", [None, [], {"protocolVersion": True}, {"protocolVersion": 2}])
def test_initialize_rejects_a_malformed_or_unsupported_response(response):
    with pytest.raises(KiroError):
        _validate_initialize(response)


@pytest.mark.parametrize(
    "path",
    [
        ("loadSession",),
        ("promptCapabilities", "image"),
        ("promptCapabilities", "embeddedContext"),
        ("mcpCapabilities", "http"),
        ("sessionCapabilities", "list"),
        ("sessionCapabilities", "delete"),
        ("sessionCapabilities", "additionalDirectories"),
        ("sessionCapabilities", "close"),
    ],
    ids=lambda path: ".".join(path),
)
def test_initialize_rejects_a_missing_required_capability(path):
    response = initialized()
    node = response["agentCapabilities"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = None

    with pytest.raises(KiroError, match=path[-1]):
        _validate_initialize(response)
