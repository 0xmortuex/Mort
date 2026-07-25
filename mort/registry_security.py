"""Authenticated Mort registry format, transparency log, and revocation."""

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DEFAULT_OPERATOR_KEYS = {
    "mort-registry-2026-1": "ulXWv5WgFfK+NnIOYEAjACtMzTXfMZ7z3SfNM8hp/SI=",
}
EMPTY_LOG_ROOT = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RegistrySecurityError(ValueError):
    pass


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _decode_key(value, label):
    if not isinstance(value, str):
        raise RegistrySecurityError(f"{label} must be Base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise RegistrySecurityError(f"{label} is not valid Base64") from error
    if len(decoded) != 32:
        raise RegistrySecurityError(f"{label} must contain 32 bytes")
    return decoded


def _decode_signature(value, label):
    if not isinstance(value, str):
        raise RegistrySecurityError(f"{label} must be Base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise RegistrySecurityError(f"{label} is not valid Base64") from error
    if len(decoded) != 64:
        raise RegistrySecurityError(f"{label} must contain 64 bytes")
    return decoded


def _verify(public_key, signature, message, label):
    try:
        Ed25519PublicKey.from_public_bytes(_decode_key(public_key, label)).verify(
            _decode_signature(signature, label + " signature"), message
        )
    except InvalidSignature as error:
        raise RegistrySecurityError(f"{label} signature is invalid") from error


def _timestamp(value, label):
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise RegistrySecurityError(f"{label} must be a canonical UTC timestamp")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise RegistrySecurityError(
            f"{label} must be a valid UTC timestamp") from error


def record_payload(package, version, record):
    return {
        "commit": record["commit"],
        "content_hash": record["content_hash"],
        "git": record["git"],
        "key_id": record["key_id"],
        "package": package,
        "published_at": record["published_at"],
        "ref": record["ref"],
        "version": version,
    }


def entry_payload(entry):
    return {key: value for key, value in entry.items() if key != "entry_hash"}


def checkpoint_payload(checkpoint):
    return {
        "format": 2,
        "key_id": checkpoint["key_id"],
        "log_root": checkpoint["log_root"],
        "log_size": checkpoint["log_size"],
        "publishers_hash": checkpoint["publishers_hash"],
    }


def publisher_identity(publisher):
    return {
        "algorithm": publisher["algorithm"],
        "public_key": publisher["public_key"],
    }


def _validate_publishers(publishers):
    if not isinstance(publishers, dict):
        raise RegistrySecurityError("publishers must be an object")
    for key_id, key in publishers.items():
        if not isinstance(key_id, str) or not key_id or not isinstance(key, dict):
            raise RegistrySecurityError("publisher key records must be named objects")
        allowed = {"algorithm", "public_key", "status", "revoked_at", "reason"}
        if set(key) - allowed:
            raise RegistrySecurityError(f"publisher key {key_id!r} has unknown fields")
        if key.get("algorithm") != "ed25519":
            raise RegistrySecurityError(f"publisher key {key_id!r} is not Ed25519")
        _decode_key(key.get("public_key"), f"publisher key {key_id!r}")
        if key.get("status") not in ("active", "revoked"):
            raise RegistrySecurityError(f"publisher key {key_id!r} has invalid status")
        if key["status"] == "active" and (
                "revoked_at" in key or "reason" in key):
            raise RegistrySecurityError(
                f"active publisher key {key_id!r} has revocation metadata")
        if key["status"] == "revoked" and not (
            isinstance(key.get("revoked_at"), str) and isinstance(key.get("reason"), str)
        ):
            raise RegistrySecurityError(
                f"revoked publisher key {key_id!r} needs revoked_at and reason"
            )
        if key["status"] == "revoked":
            _timestamp(key["revoked_at"], f"publisher key {key_id!r} revoked_at")


def verify_registry(index, trusted_operator_keys, source="registry"):
    """Verify format 2 records, log chain, revocations, and checkpoint."""
    if not isinstance(index, dict) or index.get("format") != 2:
        raise RegistrySecurityError(f"{source} must use authenticated format 2")
    expected_top = {"format", "publishers", "packages", "log", "checkpoint"}
    if set(index) != expected_top:
        raise RegistrySecurityError(f"{source} has missing or unknown top-level fields")
    publishers = index["publishers"]
    _validate_publishers(publishers)
    packages = index["packages"]
    if not isinstance(packages, dict):
        raise RegistrySecurityError("packages must be an object")

    records = {}
    required_record = {
        "git", "ref", "commit", "content_hash", "published_at", "key_id", "signature"
    }
    for package, package_data in packages.items():
        if not isinstance(package, str) or not _NAME_RE.fullmatch(package):
            raise RegistrySecurityError(f"invalid package name {package!r}")
        if not isinstance(package_data, dict) or set(package_data) != {"versions"}:
            raise RegistrySecurityError(
                f"package {package!r} has missing or unknown fields")
        versions = package_data.get("versions") if isinstance(package_data, dict) else None
        if not isinstance(versions, dict):
            raise RegistrySecurityError(f"package {package!r} needs a versions object")
        for version, record in versions.items():
            label = f"record {package}@{version}"
            match = _SEMVER_RE.fullmatch(version) if isinstance(version, str) else None
            if match is None or any(
                    item.isdigit() and len(item) > 1 and item.startswith("0")
                    for item in (() if match is None or match.group(4) is None
                                 else match.group(4).split("."))):
                raise RegistrySecurityError(f"{label} has an invalid semantic version")
            if not isinstance(record, dict) or set(record) != required_record:
                raise RegistrySecurityError(f"{label} has missing or unknown fields")
            if (not isinstance(record["git"], str) or not record["git"]
                    or record["git"].startswith("-")):
                raise RegistrySecurityError(f"{label} has no Git source")
            if (not isinstance(record["ref"], str) or not record["ref"]
                    or record["ref"].startswith("-")):
                raise RegistrySecurityError(f"{label} has no Git ref")
            if not _COMMIT_RE.fullmatch(record["commit"]):
                raise RegistrySecurityError(f"{label} has an invalid immutable commit")
            if not _HASH_RE.fullmatch(record["content_hash"]):
                raise RegistrySecurityError(f"{label} has an invalid content hash")
            _timestamp(record["published_at"], f"{label} published_at")
            key = publishers.get(record["key_id"])
            if key is None:
                raise RegistrySecurityError(f"{label} uses an unknown publisher key")
            _verify(
                key["public_key"], record["signature"],
                canonical_json(record_payload(package, version, record)), label,
            )
            records[(package, version)] = sha256_json(record)

    log = index["log"]
    if not isinstance(log, list):
        raise RegistrySecurityError("transparency log must be an array")
    previous = EMPTY_LOG_ROOT
    published = {}
    added = set()
    revoked = set()
    for sequence, entry in enumerate(log):
        if (not isinstance(entry, dict)
                or type(entry.get("sequence")) is not int
                or entry["sequence"] != sequence):
            raise RegistrySecurityError("transparency log sequence is not contiguous")
        if entry.get("previous_hash") != previous:
            raise RegistrySecurityError("transparency log hash chain is broken")
        calculated = sha256_json(entry_payload(entry))
        if entry.get("entry_hash") != calculated:
            raise RegistrySecurityError("transparency log entry hash is invalid")
        event = entry.get("event")
        if event == "add-key":
            if set(entry) != {
                    "sequence", "previous_hash", "event", "key_id",
                    "key_hash", "entry_hash"}:
                raise RegistrySecurityError(
                    "publisher-key event has missing or unknown fields")
            key_id = entry.get("key_id")
            key = publishers.get(key_id)
            if (key is None
                    or entry.get("key_hash") != sha256_json(publisher_identity(key))):
                raise RegistrySecurityError(
                    "publisher-key event does not match key metadata")
            if key_id in added:
                raise RegistrySecurityError("publisher key is added twice")
            added.add(key_id)
        elif event == "publish":
            if set(entry) != {
                    "sequence", "previous_hash", "event", "package", "version",
                    "record_hash", "entry_hash"}:
                raise RegistrySecurityError(
                    "publish event has missing or unknown fields")
            key = (entry.get("package"), entry.get("version"))
            if key not in records or entry.get("record_hash") != records[key]:
                raise RegistrySecurityError("publish log entry does not match its record")
            if key in published:
                raise RegistrySecurityError("registry record appears twice in the log")
            if index["packages"][key[0]]["versions"][key[1]]["key_id"] not in added:
                raise RegistrySecurityError(
                    "record was published before its publisher key was logged")
            published[key] = calculated
        elif event == "revoke-key":
            if set(entry) != {
                    "sequence", "previous_hash", "event", "key_id",
                    "revoked_at", "reason", "entry_hash"}:
                raise RegistrySecurityError(
                    "key-revocation event has missing or unknown fields")
            key_id = entry.get("key_id")
            key = publishers.get(key_id)
            if key is None or key.get("status") != "revoked":
                raise RegistrySecurityError("key-revocation event has no revoked key")
            if (entry.get("revoked_at") != key.get("revoked_at")
                    or entry.get("reason") != key.get("reason")):
                raise RegistrySecurityError("key-revocation event does not match key metadata")
            if key_id in revoked:
                raise RegistrySecurityError("publisher key is revoked twice")
            revoked.add(key_id)
        else:
            raise RegistrySecurityError(f"unknown transparency event {event!r}")
        previous = calculated
    if set(published) != set(records):
        raise RegistrySecurityError("not every registry record is in the transparency log")
    if added != set(publishers):
        raise RegistrySecurityError("not every publisher key is in the transparency log")
    expected_revoked = {
        key_id for key_id, key in publishers.items() if key["status"] == "revoked"
    }
    if revoked != expected_revoked:
        raise RegistrySecurityError("not every revoked key is in the transparency log")

    checkpoint = index["checkpoint"]
    if not isinstance(checkpoint, dict) or set(checkpoint) != {
        "key_id", "log_size", "log_root", "publishers_hash", "signature"
    }:
        raise RegistrySecurityError("checkpoint has missing or unknown fields")
    if (type(checkpoint["log_size"]) is not int
            or checkpoint["log_size"] != len(log)
            or checkpoint["log_root"] != previous):
        raise RegistrySecurityError("checkpoint does not match the transparency log")
    if checkpoint["publishers_hash"] != sha256_json(publishers):
        raise RegistrySecurityError("checkpoint does not match publisher keys")
    operator_key = trusted_operator_keys.get(checkpoint["key_id"])
    if operator_key is None:
        raise RegistrySecurityError("checkpoint uses an untrusted operator key")
    _verify(
        operator_key, checkpoint["signature"],
        canonical_json(checkpoint_payload(checkpoint)), "registry checkpoint",
    )
    return index


def verify_append_only(previous, current):
    """Reject rollback and rewriting of an already cached transparency prefix."""
    old_log = previous["log"]
    new_log = current["log"]
    if len(new_log) < len(old_log):
        raise RegistrySecurityError("registry transparency log rollback detected")
    if new_log[:len(old_log)] != old_log:
        raise RegistrySecurityError("registry transparency history rewrite detected")
