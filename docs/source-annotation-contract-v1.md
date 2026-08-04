# Source annotation contract, version 1

Status: version 1.0. This is the grammar and conformance baseline for
[#84](https://github.com/lbliii/murlocs/issues/84). It is deliberately narrower than a resolver:
it neither adds a manifest field nor discovers or activates source annotations in Murlocs. The
[authority model](source-annotation-authority.md) remains controlling: a parsed marker is an
untrusted attachment candidate, never guidance, proof, approval, ownership, a suppression, or a
command.

## 1. Terms and compatibility

A **declaration** is a future reviewed manifest/layer relationship with a stable identifier. An
**annotation** is a source comment that names that identifier. A **binding** is the resolver's
pairing of one declaration and one annotation location. No binding exists in this release.

Version 1 has exactly one relationship kind: `evidence`. It means that a reviewed declaration may
point to a source location as inspectable evidence. It is not semantic proof: marker presence does
not show that code is correct, a check ran, or an invariant is true. Declarations continue to own
the statement, scope, severity, owners, and relationship kind.

The namespace, version, directive, and identifier compare byte-for-byte; they are case-sensitive.
Future versions use a new version token and cannot reinterpret v1 text. A v1 reader reports an
otherwise well-formed `murlocs:annotation/vN` marker for `N != 1` as
`annotation.unknown-version`; it must not fall back to textual-anchor matching. A v1 unknown
directive is `annotation.unknown-kind`. Future contracts may add kinds only in a later version
unless they explicitly retain v1's byte grammar and finding behavior.

Existing `evidence_file` plus `anchor` textual evidence remains independent and unchanged. It can
coexist with a later typed binding to the same file, but neither form upgrades, replaces, or
silently deduplicates the other. Migration requires an explicit future reviewed compatibility rule
and must retain the old anchor until that rule's checks pass.

## 2. Wire grammar

All candidate files are decoded as UTF-8 without a BOM. The marker is ASCII and occupies the whole
recognized comment body; no leading/trailing whitespace, tabs, newline, or trailing prose is
allowed. The comment body is the text after a line-comment introducer or inside a single-line block
wrapper, excluding one optional ASCII space immediately after the opener and immediately before a
block closer. Line endings (`LF` and `CRLF`) are transport only and are not part of the body.

```ebnf
annotation = "murlocs:annotation/v1", SP, "evidence", SP, quoted-identifier ;
quoted-identifier = DQUOTE, identifier, DQUOTE ;
identifier = alpha, { alnum | separator, alnum } ;
alpha = "a"…"z" ;
alnum = alpha | "0"…"9" ;
separator = "." | "_" | "-" ;
SP = " " ;
```

An identifier is 1–128 UTF-8 bytes (therefore ASCII under this grammar); a complete comment body
is at most 256 UTF-8 bytes. Quotes are mandatory and have no escape syntax. An identifier cannot
start/end with a separator or contain adjacent separators. Unicode is permitted in surrounding
source but not in this version's namespace, directive, or identifier. Parsers must not normalize
Unicode, case, whitespace, or quote forms.

Supported wrappers are `#`, `//`, `--`, and `;` line comments, plus single-line `/* … */` and
`<!-- … -->` block comments. A wrapper only establishes a comment boundary; no wrapper has greater
authority or different relationship semantics. A language scanner may offer a supported wrapper
only where that language treats it as a real comment. It must not recognize marker-looking string
literals, here-documents, documentation code fences, quoted examples, or multi-line block comments
as annotations. Wrapper recognition is syntax-aware; the grammar above consumes only the resulting
comment body.

For example, the complete body of `# murlocs:annotation/v1 evidence "api.contract"` is the valid
marker `murlocs:annotation/v1 evidence "api.contract"`. This documentation example is prose, not a
declaration or discovered source comment.

## 3. Discovery, identity, and placement

V1 defines a fail-closed discovery sequence for a future resolver:

1. Start only from a reviewed declaration's finite, repository-relative declared-file list. V1 does
   not add a manifest field or authorize a recursive source-tree scan, glob expansion, ignore-file
   override, network fetch, or external command.
2. Canonicalize each selected path against the revision-pinned repository root. Reject absolute,
   traversal, duplicate-canonical, symlink-escaping, submodule, generated, vendor, ignored, binary,
   undecodable, and out-of-snapshot candidates before comment parsing.
3. Apply the language's syntax-aware comment scanner. Parse only whole comment bodies with section
   2's grammar; retain no surrounding source prose.
4. Match the byte-exact identifier, kind, and contract version to a declaration. A successful
   binding records only contract version, kind, identifier, repository-relative file path, and
   1-based physical line number. It never records a column, source snippet, URL, prompt, or command.

The identifier is the declaration identity within the resolver's declared relationship domain. Each
declared identifier must have exactly one binding in that domain; duplicate declarations are invalid,
as are duplicate matching comments. A valid marker with no declaration is an orphan. A declaration
with no marker is missing. A marker in a real comment but outside a selected declared file is
misplaced, not a reason to expand discovery. Moving a marker changes only its attachment location;
the resolver must re-resolve it against the reviewed revision rather than assume formatter movement
preserves approval.

Generated and vendored files are excluded in v1, including generated files whose text looks
hand-authored. A future reviewed declaration may define a narrowly auditable exception only in a
later contract; it cannot make excluded source authoritative. Symlinks are never followed outside
the revision-pinned root, and an implementation must reject a race or changed canonical target.

## 4. Limits and findings

Before opening files, a v1 implementation must cap at 256 declared files, 16 path components per
path, and 64 KiB per candidate. Across one resolution it must cap 4 MiB read, 1,024 candidate
comments, and 2 seconds of parser work. A limit is inclusive; exceeding it emits the stable finding
below and produces no partial binding. Implementations must keep only bounded identifiers and
locations in diagnostics, never unbounded source content.

| Input/state | Required v1 finding or outcome |
| --- | --- |
| Selected declaration has one exact matching valid marker | binding candidate; still inert until a future resolver is authorized |
| Declared identifier has no marker | `annotation.missing` |
| More than one matching marker or declaration | `annotation.duplicate` |
| Namespace-shaped text violates v1 grammar, bytes, quoting, length, or line boundary | `annotation.malformed` |
| `murlocs:annotation/vN` where `N != 1` | `annotation.unknown-version` |
| V1 marker uses a directive other than `evidence` | `annotation.unknown-kind` |
| Valid marker has no reviewed declaration | `annotation.orphaned` |
| Valid marker is in an undeclared file/location | `annotation.misplaced` |
| Unsupported wrapper/language or marker-looking non-comment text | `annotation.unsupported` or inert; never guess |
| Generated, vendored, ignored, submodule, symlink-escaping, or path-escaping candidate | `annotation.excluded` |
| Non-text or invalid UTF-8 candidate | `annotation.undecodable` |
| File/count/byte/depth/time limit or snapshot race | `annotation.resource-limit` |

Findings are deterministic and use the first applicable boundary outcome: path and exclusion checks
precede decoding; decoding precedes scanning; scanning precedes grammar; grammar precedes binding.
An implementation may add bounded path/line metadata but must not expose source prose. Any finding
leaves guidance unchanged and cannot turn absence, a deletion, or a malformed marker into approval.

## 5. Conformance corpus

`tests/fixtures/source-annotation-contract/v1/cases.json` is the language-neutral normative corpus.
Each case supplies an already syntax-recognized comment body (or explicitly says it is inert), its
wrapper, source location where applicable, and exactly one expected grammar result. The adjacent
fixtures cover Python, Go, JavaScript, TypeScript, Rust, shell, YAML, HTML/Markdown, CRLF, Unicode,
formatter movement, string literals, and documentation examples. `tests/test_source_annotation_contract.py`
executes the reference grammar against every case and verifies locations and CRLF bytes.

The small `murlocs.source_annotations.parse_v1_comment` reference accepts a comment body only. It is
not a source scanner, declared-file discoverer, manifest feature, or binding resolver. An independent
implementation must produce the corpus's expected result for each selected body and must separately
apply this document's syntax-aware scanner, discovery, boundary, uniqueness, and authority rules.
