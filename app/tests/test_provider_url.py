"""模型接入地址与 MCP 开关的校验（0021 §二之 2，账 93）。"""

from __future__ import annotations

import pytest

from app.domain.workbench.provider_url import (
    normalize_mcp_sources,
    validate_provider_base_url,
)


@pytest.mark.parametrize(
    "url, kept",
    [
        ("https://api.moonshot.cn/v1", "https://api.moonshot.cn/v1"),
        (
            "https://proxy.example.com:8443/openai/v1/",
            "https://proxy.example.com:8443/openai/v1",
        ),
        ("  https://api.openai.com/v1  ", "https://api.openai.com/v1"),
    ],
)
def test_public_https_endpoints_pass_and_lose_the_trailing_slash(url, kept):
    assert validate_provider_base_url(url) == kept


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://user:pw@api.example.com/v1",
        "https://api.example.com/v1?x=1",
        "https://api.example.com/v1#f",
        "https://localhost:8000/v1",
        "https://127.0.0.1/v1",
        "https://10.1.2.3/v1",
        "https://192.168.1.5:443/v1",
        "https://169.254.169.254/latest",
        "https://[::1]/v1",
        "https://gateway.internal/v1",
        "https://proxy/v1",
        "https://api.example.com/v 1",
        "",
        "https://" + "a" * 520 + ".com",
    ],
)
def test_private_local_or_malformed_endpoints_are_refused(url):
    with pytest.raises(ValueError):
        validate_provider_base_url(url)


def test_mcp_sources_default_to_all_on_and_refuse_unknown_keys():
    assert normalize_mcp_sources(None) == {"knowbase": True}
    assert normalize_mcp_sources({"knowbase": False}) == {"knowbase": False}
    assert normalize_mcp_sources({}) == {"knowbase": True}
    with pytest.raises(ValueError):
        normalize_mcp_sources({"records": True})
