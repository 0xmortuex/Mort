# Vendored TLS inputs

Mort's hosted TLS backend is built on first use from two checksum-pinned,
upstream release artifacts:

- `mbedtls-3.6.6.tar.bz2` is the official Mbed TLS 3.6.6 LTS archive.
  SHA-256:
  `8fb65fae8dcae5840f793c0a334860a411f884cc537ea290ce1c52bb64ca007a`.
  Upstream: <https://github.com/Mbed-TLS/mbedtls/releases/tag/mbedtls-3.6.6>
- `cacert.pem` is the Mozilla CA bundle distributed by certifi 2026.6.17.
  SHA-256:
  `bbc7e9c01d7551bb8a159b5dedd989b8ee3ce105aff522b68eb1b01bf854cab0`.
  Upstream: <https://pypi.org/project/certifi/2026.6.17/>

`MBEDTLS-LICENSE` and `CERTIFI-LICENSE` contain the corresponding license
texts. `mort_tls.c` is Mort's small integration layer. The compiler verifies
both payload hashes before extracting or compiling anything and caches the
result by compiler identity, platform, architecture, and build flags.
