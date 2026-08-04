# Runtime build identity

`murlocs version` answers two deliberately separate questions about the code
that is running:

```text
murlocs 0.1.0
build: development (sha256:...)
verification: unverified
installation: editable
```

Use `murlocs version --format json` for host integrations. The version command
is local-only: it does not run Git, invoke an installer, or contact a network.

## The v1 contract

The structured result contains a stable project and public package version,
plus two independent records:

```json
{
  "schema_version": 1,
  "project": "murlocs",
  "version": "0.1.0",
  "build": {
    "kind": "development",
    "id": "sha256:<64 lowercase hexadecimal characters>",
    "verification": "unverified"
  },
  "installation": {
    "kind": "editable",
    "editable": true,
    "source_revision": null,
    "archive_hash": null
  }
}
```

`build.id` is an opaque SHA-256 fingerprint of the regular files in the
imported package and Murlocs' expected console-entry-point declarations. Those
declarations deliberately mirror the checked-in `pyproject.toml` executable
surface; they are not read from installed distribution metadata. It changes
when that package content changes even if the public package version stays the
same. Bytecode caches are ignored. The implementation applies file-count and
byte limits, refuses symlinks and detected file races, and returns
`build.kind: "unknown"` rather than following an unsafe path or hashing an
unbounded tree.

`release` only means that the local facts look release-shaped: a final package
version, a safely read package, and no local, editable, VCS, development,
local-version, alpha, beta, or release-candidate indicator. `development` is
returned for local-directory, editable, and VCS installs, and for PEP 440
development, local, alpha, beta, and release-candidate versions.
Missing or malformed metadata, or unsafe package contents, result in
`unknown`.

The command deliberately keeps `verification: "unverified"`. A package can
be rebuilt locally with the same public version; no self-reported metadata can
prove it came from an official publisher. Calling a build *official* requires
a separate trust check, such as a registry digest plus a verified publisher
attestation or signature.

## Installation provenance

Python installers record direct installation origins in the optional
`direct_url.json` file defined by [PEP 610](https://peps.python.org/pep-0610/).
Murlocs reads only the classification fields and never returns a URL or local
path:

| PEP 610 record | Installation kind | Exposed fields |
| --- | --- | --- |
| `dir_info`, non-editable | `local-directory` | `editable: false` |
| `dir_info.editable: true` | `editable` | `editable: true` |
| `vcs_info` | `vcs` | immutable `commit_id` as `source_revision` |
| `archive_info` | `archive` | optional archive hash |
| no `direct_url.json` | `index-or-unknown` | none |

PEP 610 requires installers not to create `direct_url.json` for ordinary
name-and-version requirements. Its absence therefore cannot prove which index
was used, or that an artifact was official. Installers also must not infer VCS
data for a local directory that happens to contain a checkout; Murlocs follows
that rule.

## Reusable Python-tool pattern

For another Python CLI, preserve the public PEP 440 distribution version for
compatibility and add a small, typed identity record beside it:

1. Keep `--version` stable for scripts that already parse it.
2. Add `tool version --format json` with a content-derived opaque build ID and
   a strictly redacted PEP 610 installation classification.
3. Treat a final version plus a missing direct-origin file as only a
   release-shaped, unverified result. Verify registry attestations separately
   before making official-publisher claims.
4. For durable launchers or hooks, pin the opaque build ID in addition to the
   public version. Editable installs may change their source after install, so
   compare a fresh runtime identity at each check.

This project intentionally does not use VCS-derived dynamic package versions
as the identity mechanism. Tools that choose Hatch VCS or setuptools-scm for
automatic PEP 440 development versions still need the same installation and
attestation boundary. Source archives need a build stamp embedded at release
time if they must retain release provenance after Git is unavailable; runtime
inspection cannot recreate that information.
