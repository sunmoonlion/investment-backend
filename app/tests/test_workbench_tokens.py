"""D10：工作台签发的 ES256 令牌——三种受众、公钥可验、过期与受众错拒绝、JWKS 只含公钥。"""

from __future__ import annotations

import time

import pytest
from joserfc.errors import BadSignatureError, ExpiredTokenError

from app.application.workbench.tokens import (
    TokenIssuer,
    generate_private_key_pem,
    verify,
)


def test_issue_and_verify_three_audiences():
    pem = generate_private_key_pem()
    issuer = TokenIssuer(pem, issuer="wb-test")
    pub = issuer.public_pem()
    agent = issuer.agent_token("u-abc")
    sandbox = issuer.sandbox_token("u-abc", sandbox="u-abc")
    knowledge = issuer.knowledge_token("u-abc", sandbox="u-abc", tools=["run_sql"])
    a = verify(agent.token, pub, audience="relay", issuer="wb-test")
    assert a["sub"] == "u-abc" and a["role"] == "agent" and a["jti"] == agent.jti
    s = verify(sandbox.token, pub, audience="relay")
    assert s["role"] == "sandbox" and s["sandbox"] == "u-abc"
    k = verify(knowledge.token, pub, audience="knowledge")
    assert k["tools"] == ["run_sql"] and k["sub"] == "u-abc"
    with pytest.raises(ValueError, match="audience"):
        verify(agent.token, pub, audience="knowledge")
    with pytest.raises(ValueError, match="issuer"):
        verify(agent.token, pub, audience="relay", issuer="other")
    expired = issuer.agent_token("u-abc", ttl_seconds=-5)
    with pytest.raises((ExpiredTokenError, ValueError)):
        verify(expired.token, pub, audience="relay")
    other = TokenIssuer(generate_private_key_pem())
    with pytest.raises(BadSignatureError):
        verify(agent.token, other.public_pem(), audience="relay")
    jwks = issuer.public_jwks()
    assert (
        jwks["keys"][0]["kty"] == "EC"
        and "d" not in jwks["keys"][0]
        and jwks["keys"][0]["kid"] == issuer.kid
    )
    assert agent.expires_at > int(time.time())
