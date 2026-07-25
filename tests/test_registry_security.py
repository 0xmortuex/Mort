"""Adversarial tests for Mort's authenticated package registry."""

import base64
import copy
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mort.registry_security import (
    EMPTY_LOG_ROOT,
    RegistrySecurityError,
    canonical_json,
    checkpoint_payload,
    entry_payload,
    publisher_identity,
    record_payload,
    sha256_json,
    verify_append_only,
    verify_registry,
)
from tools import registry_admin


def _public(key):
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )).decode("ascii")


def _sign(key, value):
    return base64.b64encode(key.sign(canonical_json(value))).decode("ascii")


def _private(key):
    return base64.b64encode(key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode("ascii")


def registry_fixture(revoked=False):
    operator = Ed25519PrivateKey.generate()
    publisher = Ed25519PrivateKey.generate()
    key_id = "publisher-1"
    record = {
        "git": "https://example.test/owner/utility.git",
        "ref": "v1.4.0",
        "commit": "a" * 40,
        "content_hash": "b" * 64,
        "published_at": "2026-07-25T12:00:00Z",
        "key_id": key_id,
    }
    record["signature"] = _sign(
        publisher, record_payload("utility", "1.4.0", record))
    publishers = {
        key_id: {
            "algorithm": "ed25519",
            "public_key": _public(publisher),
            "status": "revoked" if revoked else "active",
        }
    }
    if revoked:
        publishers[key_id].update({
            "revoked_at": "2026-07-25T13:00:00Z",
            "reason": "test compromise",
        })
    add_key = {
        "sequence": 0,
        "previous_hash": EMPTY_LOG_ROOT,
        "event": "add-key",
        "key_id": key_id,
        "key_hash": sha256_json(publisher_identity(publishers[key_id])),
    }
    add_key["entry_hash"] = sha256_json(entry_payload(add_key))
    publish = {
        "sequence": 1,
        "previous_hash": add_key["entry_hash"],
        "event": "publish",
        "package": "utility",
        "version": "1.4.0",
        "record_hash": sha256_json(record),
    }
    publish["entry_hash"] = sha256_json(entry_payload(publish))
    log = [add_key, publish]
    if revoked:
        revoke = {
            "sequence": 2,
            "previous_hash": publish["entry_hash"],
            "event": "revoke-key",
            "key_id": key_id,
            "revoked_at": publishers[key_id]["revoked_at"],
            "reason": publishers[key_id]["reason"],
        }
        revoke["entry_hash"] = sha256_json(entry_payload(revoke))
        log.append(revoke)
    index = {
        "format": 2,
        "publishers": publishers,
        "packages": {"utility": {"versions": {"1.4.0": record}}},
        "log": log,
    }
    checkpoint = {
        "key_id": "operator-1",
        "log_size": len(log),
        "log_root": log[-1]["entry_hash"],
        "publishers_hash": sha256_json(publishers),
    }
    checkpoint["signature"] = _sign(operator, checkpoint_payload(checkpoint))
    index["checkpoint"] = checkpoint
    return index, {"operator-1": _public(operator)}


def test_registry_verifies_signed_records_log_and_revocation():
    index, trust = registry_fixture(revoked=True)
    assert verify_registry(index, trust) is index


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("packages", "utility", "versions", "1.4.0", "commit"), "c" * 40,
         "record utility@1.4.0 signature is invalid"),
        (("log", 0, "record_hash"), "d" * 64, "entry hash is invalid"),
        (("checkpoint", "log_root"), "e" * 64,
         "checkpoint does not match"),
    ],
)
def test_registry_rejects_tampering(path, value, message):
    index, trust = registry_fixture()
    target = index
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(RegistrySecurityError, match=message):
        verify_registry(index, trust)


def test_registry_rejects_untrusted_operator():
    index, _ = registry_fixture()
    with pytest.raises(RegistrySecurityError, match="untrusted operator"):
        verify_registry(index, {})


def test_registry_append_only_rejects_rollback_and_rewrite():
    current, _ = registry_fixture(revoked=True)
    previous = copy.deepcopy(current)
    previous["log"] = previous["log"][:2]
    verify_append_only(previous, current)
    with pytest.raises(RegistrySecurityError, match="rollback"):
        verify_append_only(current, previous)
    rewritten = copy.deepcopy(current)
    rewritten["log"][0]["package"] = "other"
    with pytest.raises(RegistrySecurityError, match="history rewrite"):
        verify_append_only(current, rewritten)


def test_registry_administration_lifecycle(tmp_path, monkeypatch):
    operator = Ed25519PrivateKey.generate()
    publisher = Ed25519PrivateKey.generate()
    path = tmp_path / "index.json"
    index = {"format": 2, "publishers": {}, "packages": {}, "log": []}
    registry_admin._checkpoint(index, "operator-1", operator)
    path.write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setenv("MORT_REGISTRY_OPERATOR_KEY", _private(operator))
    monkeypatch.setenv("MORT_PUBLISHER_KEY", _private(publisher))
    common = {
        "index": path,
        "key_id": "operator-1",
        "key_environment": "MORT_REGISTRY_OPERATOR_KEY",
    }
    registry_admin.command_add_publisher(SimpleNamespace(
        **common, publisher_key_id="publisher-1", public_key=_public(publisher)))
    registry_admin.command_publish(SimpleNamespace(
        **common,
        publisher_key_id="publisher-1",
        publisher_key_environment="MORT_PUBLISHER_KEY",
        package="utility",
        version="1.0.0",
        git="https://example.test/utility.git",
        ref="v1.0.0",
        commit="a" * 40,
        content_hash="b" * 64,
        published_at="2026-07-25T12:00:00Z",
    ))
    registry_admin.command_revoke(SimpleNamespace(
        **common,
        publisher_key_id="publisher-1",
        revoked_at="2026-07-25T13:00:00Z",
        reason="test compromise",
    ))
    result = json.loads(path.read_text(encoding="utf-8"))
    verify_registry(result, {"operator-1": _public(operator)})
    assert [item["event"] for item in result["log"]] == [
        "add-key", "publish", "revoke-key"]
    assert result["publishers"]["publisher-1"]["status"] == "revoked"
