# Mort security policy

## Supported versions

Mort is an alpha project. Security fixes are made for the latest published
minor release and its patch releases. The preceding minor release receives
critical fixes for 90 days after it is superseded; older releases are
unsupported.

| Release line | Security support |
| --- | --- |
| 0.43.x | Full |
| 0.42.x | Critical fixes until 2026-10-23 |
| 0.41.x and older | Unsupported |

Users should upgrade to the newest release before reporting an issue that may
already be fixed. Unsupported versions can still be reported when the same
vulnerability is present in a supported version.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use Mort's
[private vulnerability report](https://github.com/0xmortuex/Mort/security/advisories/new)
to share the affected version, reproduction steps or proof of concept, impact,
and any suggested remediation. Private vulnerability reporting is enabled for
the repository.

Reports may cover the compiler, generated code, package resolver and registry
format, standard library, TLS/cryptography integration, release artifacts, or
project-controlled CI and distribution infrastructure. Reports about an
upstream dependency should explain how Mort is affected.

## Response process

The project targets these response times:

- acknowledge a complete report within three business days;
- provide an initial severity and scope assessment within seven days;
- target a fix or mitigation within 14 days for critical issues, 30 days for
  high-severity issues, and 90 days for lower-severity issues;
- keep the reporter informed when the assessment or timeline changes.

These are targets rather than guarantees, especially when a coordinated
upstream fix is required. The project will validate the report, prepare a fix
and regression test, privately request a CVE when appropriate, publish patched
artifacts, and then release an advisory describing affected versions,
mitigations, and credit. Public disclosure is coordinated with the reporter;
an immediately exploited issue may require an accelerated advisory.

Good-faith research that avoids privacy violations, service disruption, data
destruction, and access beyond what is needed to demonstrate the issue is
welcome. Never include secrets or personal data in a report.
