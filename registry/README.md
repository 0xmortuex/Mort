# Mort package registry

This directory is the canonical public Mort registry index. Each package entry
maps semantic versions to immutable Git sources:

```json
{
  "packages": {
    "example": {
      "versions": {
        "1.2.0": {
          "git": "https://github.com/owner/example-mort.git",
          "ref": "v1.2.0"
        }
      }
    }
  }
}
```

Projects depend on a package with `name = "registry:example@^1.2.0"` under
`[dependencies]`. A project may set `registry.url` and `registry.mirrors` in
`mort.toml`; `MORT_REGISTRY_URL` and the path-separated `MORT_MIRRORS`
environment variable provide machine-wide overrides. `mortc fetch --offline`
uses only cached index data, cached Git checkouts, and those mirrors.
