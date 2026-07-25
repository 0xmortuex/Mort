#!/usr/bin/env python3
"""Offline administration for Mort's authenticated registry.

Private Ed25519 keys are accepted only through environment variables and are
never written to disk by this tool.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mort.registry_security import (
    EMPTY_LOG_ROOT,
    canonical_json,
    entry_payload,
    publisher_identity,
    record_payload,
    sha256_json,
    verify_registry,
)


def _private_key(environment_name):
    encoded = os.environ.get(environment_name)
    if not encoded:
        raise SystemExit(f"{environment_name} is required")
    try:
        raw = base64.b64decode(encoded, validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError) as error:
        raise SystemExit(f"{environment_name} must be a Base64 Ed25519 key") from error


def _public_text(key):
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _signature(key, value):
    return base64.b64encode(key.sign(canonical_json(value))).decode("ascii")


def _write(path, value):
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _checkpoint(index, key_id, key):
    checkpoint = {
        "key_id": key_id,
        "log_size": len(index["log"]),
        "log_root": (
            index["log"][-1]["entry_hash"] if index["log"] else EMPTY_LOG_ROOT
        ),
        "publishers_hash": sha256_json(index["publishers"]),
    }
    checkpoint["signature"] = _signature(key, {
        "format": 2,
        **checkpoint,
    })
    index["checkpoint"] = checkpoint


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _append(index, event):
    event = {
        "sequence": len(index["log"]),
        "previous_hash": (
            index["log"][-1]["entry_hash"] if index["log"] else EMPTY_LOG_ROOT
        ),
        **event,
    }
    event["entry_hash"] = sha256_json(entry_payload(event))
    index["log"].append(event)


def _operator_update(arguments, mutate):
    index = _load(arguments.index)
    operator = _private_key(arguments.key_environment)
    mutate(index)
    _checkpoint(index, arguments.key_id, operator)
    verify_registry(index, {arguments.key_id: _public_text(operator)}, str(arguments.index))
    _write(arguments.index, index)


def command_initialize(arguments):
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    encoded_private = base64.b64encode(raw).decode("ascii")
    if arguments.github_secret:
        subprocess.run(
            ["gh", "secret", "set", arguments.github_secret],
            cwd=ROOT, input=encoded_private, text=True, check=True)
    else:
        raise SystemExit(
            "--github-secret is required so the generated private key is not printed")
    index = {"format": 2, "publishers": {}, "packages": {}, "log": []}
    _checkpoint(index, arguments.key_id, key)
    _write(arguments.index, index)
    print(_public_text(key))


def command_checkpoint(arguments):
    index = _load(arguments.index)
    _checkpoint(index, arguments.key_id, _private_key(arguments.key_environment))
    _write(arguments.index, index)


def command_add_publisher(arguments):
    def mutate(index):
        if arguments.publisher_key_id in index["publishers"]:
            raise SystemExit("publisher key already exists")
        publisher = {
            "algorithm": "ed25519",
            "public_key": arguments.public_key,
            "status": "active",
        }
        index["publishers"][arguments.publisher_key_id] = publisher
        _append(index, {
            "event": "add-key",
            "key_id": arguments.publisher_key_id,
            "key_hash": sha256_json(publisher_identity(publisher)),
        })

    _operator_update(arguments, mutate)


def command_publish(arguments):
    def mutate(index):
        publisher = _private_key(arguments.publisher_key_environment)
        metadata = index["publishers"].get(arguments.publisher_key_id)
        if metadata is None or metadata.get("status") != "active":
            raise SystemExit("publisher key is absent or revoked")
        if metadata["public_key"] != _public_text(publisher):
            raise SystemExit("publisher private key does not match registry metadata")
        versions = index["packages"].setdefault(
            arguments.package, {"versions": {}})["versions"]
        if arguments.version in versions:
            raise SystemExit("package version already exists; records are immutable")
        record = {
            "git": arguments.git,
            "ref": arguments.ref,
            "commit": arguments.commit,
            "content_hash": arguments.content_hash,
            "published_at": arguments.published_at,
            "key_id": arguments.publisher_key_id,
        }
        record["signature"] = _signature(
            publisher, record_payload(arguments.package, arguments.version, record))
        versions[arguments.version] = record
        _append(index, {
            "event": "publish",
            "package": arguments.package,
            "version": arguments.version,
            "record_hash": sha256_json(record),
        })

    _operator_update(arguments, mutate)


def command_revoke(arguments):
    def mutate(index):
        publisher = index["publishers"].get(arguments.publisher_key_id)
        if publisher is None:
            raise SystemExit("publisher key does not exist")
        if publisher["status"] == "revoked":
            raise SystemExit("publisher key is already revoked")
        publisher.update({
            "status": "revoked",
            "revoked_at": arguments.revoked_at,
            "reason": arguments.reason,
        })
        _append(index, {
            "event": "revoke-key",
            "key_id": arguments.publisher_key_id,
            "revoked_at": arguments.revoked_at,
            "reason": arguments.reason,
        })

    _operator_update(arguments, mutate)


def command_verify(arguments):
    index = _load(arguments.index)
    keys = {}
    for value in arguments.trusted_key:
        key_id, separator, public_key = value.partition(":")
        if not separator:
            raise SystemExit("trusted keys use key-id:Base64")
        keys[key_id] = public_key
    verify_registry(index, keys, str(arguments.index))
    print(
        f"verified format 2 registry: {len(index['log'])} log entries, "
        f"{sum(len(item['versions']) for item in index['packages'].values())} records")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("initialize")
    initialize.add_argument("--index", type=Path, required=True)
    initialize.add_argument("--key-id", required=True)
    initialize.add_argument("--github-secret", required=True)
    initialize.set_defaults(function=command_initialize)
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--index", type=Path, required=True)
    checkpoint.add_argument("--key-id", required=True)
    checkpoint.add_argument(
        "--key-environment", default="MORT_REGISTRY_OPERATOR_KEY")
    checkpoint.set_defaults(function=command_checkpoint)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--index", type=Path, required=True)
    common.add_argument("--key-id", required=True)
    common.add_argument("--key-environment", default="MORT_REGISTRY_OPERATOR_KEY")
    add_publisher = commands.add_parser("add-publisher", parents=[common])
    add_publisher.add_argument("--publisher-key-id", required=True)
    add_publisher.add_argument("--public-key", required=True)
    add_publisher.set_defaults(function=command_add_publisher)
    publish = commands.add_parser("publish", parents=[common])
    publish.add_argument("--package", required=True)
    publish.add_argument("--version", required=True)
    publish.add_argument("--git", required=True)
    publish.add_argument("--ref", required=True)
    publish.add_argument("--commit", required=True)
    publish.add_argument("--content-hash", required=True)
    publish.add_argument("--published-at", required=True)
    publish.add_argument("--publisher-key-id", required=True)
    publish.add_argument(
        "--publisher-key-environment", default="MORT_PUBLISHER_KEY")
    publish.set_defaults(function=command_publish)
    revoke = commands.add_parser("revoke", parents=[common])
    revoke.add_argument("--publisher-key-id", required=True)
    revoke.add_argument("--revoked-at", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.set_defaults(function=command_revoke)
    verify = commands.add_parser("verify")
    verify.add_argument("--index", type=Path, required=True)
    verify.add_argument("--trusted-key", action="append", required=True)
    verify.set_defaults(function=command_verify)
    arguments = parser.parse_args()
    arguments.function(arguments)


if __name__ == "__main__":
    main()
