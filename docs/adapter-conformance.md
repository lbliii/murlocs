# Agent-host adapter contract and conformance harness

Murlocs adapter contract version 1 is `io.murlocs.adapter`. It is a test boundary for hosts,
hooks, and CI integrations that implement the [activation lifecycle](activation-lifecycle.md). It
does not define a universal Git snapshot, install an adapter, or give an agent authority to mint
freshness evidence.

The reusable harness is `murlocs.adapter_conformance.run_adapter_conformance`. Its versioned suite
is installed with the package at `murlocs/adapter_fixtures/v1/conformance.json`, so the same bytes
can be run by the first host adapter, a second independent host, hooks, and CI.

## Trust boundary

An adapter driver receives only the agent-callable activation request. The harness gives it an
isolated repository and deterministic test controls through a separate `ConformanceContext`.
Repository root selection, token scope, state, impact dependencies, cache proofs, and manifest
identity never enter the agent request. An adversarial scenario injects a syntactically valid
state claim through that wire and requires visible rejection before any operation runs.

State and dependency tokens are opaque. They are comparable only within one adapter id, adapter
version, and session. The harness verifies behavior:

- equal state tokens surround a fresh operation;
- an impact receipt also has equal before/after dependency tokens;
- a declared external repository mutation makes the result stale and discards receipts;
- an impact-only dependency mutation can make impact stale without prescribing how its token is
  serialized; and
- cached evidence matches the complete trusted proof or is rejected before fresh work.

The small `opaque_file_token` helper exists only for conformance-driver tests. It is deliberately
named `fixture:` and is not a production token algorithm.

## Required capabilities and optional accelerators

Every version-1 descriptor declares these required capabilities in order:

1. exact `.murlocs/manifest.toml` discovery;
2. out-of-band trusted context;
3. opaque repository-state freshness;
4. impact-dependency freshness;
5. a read-only typed operation runner;
6. deadline enforcement;
7. strict structured-output parsing; and
8. typed outcome forwarding.

Adapters also declare each lifecycle event as `host-enforced`, `prompt-mediated`, or `unavailable`.
This is an honest capability statement, not a policy override. Exact-proof caching, native task
hooks, and explicitly authorized deterministic-repair dispatch are optional accelerators. The
portable root grammar covers POSIX, Windows drive, and Windows UNC roots even when the host runs on
only one platform.

Generated guidance, Git hooks, and CI remain ordered fallback capabilities. A fallback identifier
does not claim that the fallback ran. Removing an adapter leaves those repository-local paths and
the CLI usable.

## Black-box scenarios

Each scenario starts in a new temporary repository. A driver returns its trusted context and
lifecycle response as an observation; the harness then validates the observable contract and the
repository bytes. The installed suite covers:

- healthy task-start silence without asking the agent about Murlocs;
- an exact trusted cache hit and stale-proof rejection followed by fresh work;
- deterministic repair, agent action, and authority escalation as parsed outcome envelopes;
- absent Murlocs with Windows drive and UNC transport vectors;
- post-edit state races and impact-dependency races;
- unavailable, malformed-output, and deadline-timeout failures;
- pre-completion rejection of agent-supplied state evidence; and
- generated-guidance, Git-hook, and CI fallback delivery.

The operation trace is separate from retained receipts. A timed-out, malformed, or stale operation
may have run, but it cannot leave a fresh receipt. Scenario-controlled mutations are the only
repository changes allowed; any adapter-created file, modified byte, symlink, opaque command field,
or nonempty lifecycle `writes` array fails conformance.

## Driver interface

```python
from collections.abc import Mapping
from typing import Any

from murlocs.adapter_conformance import ConformanceContext, run_adapter_conformance


class MyAdapterDriver:
    def descriptor(self) -> Mapping[str, Any]:
        ...

    def invoke(
        self, request: Mapping[str, Any], context: ConformanceContext
    ) -> Mapping[str, Any]:
        # Use context.root as host-owned input. Record typed operations and expose
        # context.checkpoint(name) only at the corresponding lifecycle seam.
        ...


report = run_adapter_conformance(MyAdapterDriver())
assert report["passed"]
```

A host-specific wrapper may use native snapshots, generations, filesystem observation, or Git.
It must cause each declared checkpoint at the real before/after seam so the suite can mutate the
isolated repository during an invocation. Issue #65 supplies the first production driver; issue
#66 must run these same scenarios through a materially different host.

## Version negotiation and deprecation

The descriptor names supported activation and outcome versions independently from adapter schema
version 1. Unknown descriptor fields are rejected because this object controls trust behavior;
unsupported lifecycle or outcome versions fail visibly. Optional response extensions remain
forward compatible under their owning contracts.

An adapter may list older schema versions in `deprecated_versions`, but it cannot mark its active
version deprecated. Removing a supported version requires a later adapter-contract version and an
overlap release that still runs the previous suite. There is no silent version guessing.
