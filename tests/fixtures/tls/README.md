# TLS test certificates

These fixed RSA keys and certificates are public test fixtures. They are not
secrets and must never be used outside the Mort loopback tests.

- `ca-cert.pem` is the only test trust root.
- `server-cert.pem` is valid for `localhost` and `127.0.0.1`.
- `wrong-host-cert.pem` is signed by the same CA but valid only for
  `wrong.invalid`.
- The certificates are valid from 2025-01-01 through 2045-01-01.

The tests bind only to `127.0.0.1` and use these fixtures to prove successful
verification, hostname mismatch rejection, and untrusted-chain rejection.
