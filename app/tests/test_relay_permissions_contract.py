"""Consumer locks the relay-owned schema; never silently invent a wire shape."""

import hashlib
import json
from pathlib import Path


def test_provider_contract_lock():
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "contracts/relay-permissions.lock.json").read_text())
    canonical = json.dumps(lock["contract"], sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == lock["canonical_sha256"]
    provider = root.parents[2] / "k8s/sunmoonai/relay-platform/relay/local-permission-v1.json"
    if provider.exists():
        assert json.loads(provider.read_text()) == lock["contract"]
