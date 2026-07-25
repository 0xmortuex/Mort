# Authenticated Mort package registry

`index.json` is Mort's canonical format-2 registry. It is fail-closed:

- every package release has an Ed25519 publisher signature;
- the signed payload binds its package, version, Git URL, human-readable ref,
  exact immutable commit, full portable package-content hash, publication time,
  and publisher key;
- every publisher addition, publication, and revocation is in a hash-chained,
  append-only log;
- an Ed25519 operator checkpoint binds the log size/root and complete publisher
  key map;
- clients pin the operator public key outside the index and reject a rollback
  or rewrite of any previously cached log prefix;
- releases signed by revoked publisher keys are unavailable, and Git checkouts
  and offline mirrors are checked against the signed content hash.

The canonical operator trust root is compiled into Mort. A custom registry must
pin one or more roots in `mort.toml`:

```toml
[registry]
url = "https://packages.example/index.json"
trusted_keys = ["operator-2026:BASE64_ED25519_PUBLIC_KEY"]
mirrors = ["/srv/mort-mirror"]
```

`MORT_REGISTRY_TRUSTED_KEYS` accepts a comma-separated equivalent. Registry
keys found inside the downloaded index are never accepted as trust roots.

## Offline administration

`tools/registry_admin.py` keeps private keys out of command lines and files.
The operator key is read from `MORT_REGISTRY_OPERATOR_KEY`; publisher signing
uses `MORT_PUBLISHER_KEY`. Both values are raw 32-byte Ed25519 private keys
encoded as Base64. The tool can initialize a registry while sending the new
operator secret directly to GitHub, add publishers, publish immutable records,
revoke publishers, create checkpoints, and verify an index. Run `--help` on a
command for its exact arguments.

For example, independent verification of this registry is:

```sh
python tools/registry_admin.py verify \
  --index registry/index.json \
  --trusted-key "mort-registry-2026-1:ulXWv5WgFfK+NnIOYEAjACtMzTXfMZ7z3SfNM8hp/SI="
```

The public log is a verifiable append-only history, not a globally witnessed
transparency service. Rollback protection depends on the client's retained
last verified index; deleting that cache also deletes its remembered history.
