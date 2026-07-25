"""Unit tests for release-compatibility policy validation."""

import json

import pytest

from tests.release_compatibility import validate_deprecations, version


def test_release_version_parser_is_strict():
    assert version("v0.44.0") == (0, 44, 0)
    with pytest.raises(ValueError, match="invalid release version"):
        version("v0.44")


def test_deprecation_ledger_requires_a_full_minor_window(tmp_path):
    path = tmp_path / "deprecations.json"
    path.write_text(json.dumps({
        "schema": 1,
        "deprecations": [{
            "feature": "old-call",
            "introduced": "0.41.0",
            "removal_not_before": "0.42.0",
            "replacement": "new-call",
        }],
    }), encoding="utf-8")
    validate_deprecations(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["deprecations"][0]["removal_not_before"] = "0.41.9"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="full minor"):
        validate_deprecations(path)
