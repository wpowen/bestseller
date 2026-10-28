# Issuer archive primitive: RED contract and API

Main observed the initial RED (58 missing-module errors plus self-coverage
failure) and authorized only the new module and this owned test directory.
The primitive is implemented; main owns native inventory registration and test
execution. Existing business modules and historical pins are untouched by this
worker. These tests do not establish E05b or any native paid binding.

```python
def capture_current_issuer_archive(settings) -> dict: ...
def verify_current_issuer_archive(settings, *, capsule: Mapping[str, object]) -> dict: ...

class IssuerArchiveError(ValueError):
    code: str  # str(error) == "review_issuer_archive:" + code
```

Both functions are synchronous local-file primitives. Settings supply the
existing `artifact_store.mode/local_dir`, not a source root. There is no source
path list, epoch, expected-policy override, approval, or permissive flag input.
`capsule` is untrusted data: its hashes must match actual local files and the
internally derived native inventory. It is never paid/issuer authority itself.

**Concrete prerequisite.** The actual
`pipelines.chapter_review_recovery_source_hash` inventory must include
`review_issuer_archive.py`. Capture rejects `inventory_self_uncovered` until
main registers that dependency. The worker cannot edit this existing module.
Neither implementation nor tests may append a synthetic entry as a fallback.
The test positives explicitly require the real registration. No source change
is needed to an old manifest or historical pin: the new current inventory will
produce a new runtime hash without a hash literal referring back to itself.
This makes the proposed capture non-circular, but current registration remains
an explicit integration prerequisite, not a test waiver.

**Trusted inventory.** Read the current pipeline source from the native
installation location internally, resolve its complete static `policy_files`
expressions, and preserve directories (`services/context.py` and
`domain/context.py` are distinct). Include its fixed quality-lever expansion
and genre configuration completeness guard. Reject unknown/conditional/dynamic
inventory expressions and an unsupported hash algorithm; never execute source
to discover paths. Reproduce the actual runtime aggregate over all resolved
relative paths and original bytes. No directory sweep may expand the trusted
list. This primitive attests that fixed policy inventory only; completeness of
all E05b dependencies remains main's integration responsibility.

Static `.add/.update` statements and literal tuple/list/set comprehensions may
add further native configuration paths, including reader-first methodology
profiles. No new glob fallback is inferred: the sole expansion is the native
fixed `services/quality_levers` Python glob. Non-Python policy files are retained
byte-for-byte and checked for credential content before publication. The
artifact namespace uses only a local publication file lock, never a DB/budget
lock. An existing archive is validated, not repaired; newly written source
files are made read-only before publication. Final addresses are claimed with
atomic exclusive directory creation, then files are linked without replacement
and the manifest is published last. An interrupted partial address is rejected,
never automatically repaired. Readers may reject an in-progress address; only
a successful completed capture returns its capsule.

**Capsule and return values.** `capture_current_issuer_archive` returns
`{"status": "captured", "capsule": capsule, **denials}`. `capsule` has exactly:

```json
{
  "schema": "review-issuer-source-capsule.v1",
  "archive_ref": "review-issuer-archives/sha256/<manifest-sha256>",
  "archive_sha256": "<manifest-sha256>",
  "runtime_policy_sha256": "<original-full-runtime-hash>",
  "python_ast_sha256": "<aggregate-of-python-AST-digests>",
  "non_python_sha256": "<aggregate-of-non-python-original-byte-digests>"
}
```

Only this small capsule is intended for future embedding in a native paid
source. Original bytes, path inventories, or environment values are not embedded
in that source. Main owns the native producer and paid-hash binding; capture
does not mint those bindings.

The local content-addressed directory contains only `manifest.json` and
`sources/<original-relative-path>` for every listed source. Manifest JSON is
canonical UTF-8 (`ensure_ascii=False`, sorted keys, compact separators, no
nonfinite values, no trailing newline). Its exact keys are `schema`
(`review-issuer-source-archive.v1`), `runtime_policy_sha256`,
`python_ast_sha256`, `non_python_sha256`, and sorted `files`. Each file entry has
exactly `path`, `kind` (`python` or `non_python`), `sha256`, and `ast_sha256`
(null for non-Python). Duplicate paths/JSON keys and unknown fields are invalid.
Every file's actual bytes must match its SHA; summaries alone are insufficient.

AST digests use the complete `ast.parse(original_bytes, type_comments=True)`
tree and `ast.dump(..., include_attributes=False)`; do not remove docstrings,
constants, branches, imports, functions, or guards. Each aggregate uses sorted
relative paths, `path.encode() + b"\0"`, then the corresponding raw SHA digest.
The runtime aggregate includes every file; the other two cover Python and
non-Python partitions respectively. No permissive AST normalization is allowed.

Verification returns `status="equivalent"`, `byte_identical`,
`issued_runtime_policy_sha256`, `current_runtime_policy_sha256`, and the matching
AST/non-Python aggregates. Changed Python comments can retain AST equivalence
while the two full runtime hashes remain distinct. The path sets must be equal;
all ASTs must match and all non-Python bytes must match. Verification writes
nothing and never imports/evaluates/executes archived Python.

Both receipts explicitly set these fields to the boolean `False`:
`grade_granted`, `new_credit_allowed`, `dispatch_authorized`,
`adoption_authorized`, `unknown_reconciled`, `paid_binding_verified`,
`issued_report_verified`, and `current_applicability_checked`.

**File integrity and credentials.** Existing content-addressed originals may
be reused only when entirely intact; never overwrite or repair them. Validate
before publication so missing/invalid/secret source does not leave a partial
archive. Reject symlinks at source/manifest/archive/store paths, traversal,
non-local stores, missing material, and every extra file or unexpected directory
inside the archive. Accepting symbolic catalog keys such as `api_key_env` does
not authorize copying their environment values. Reject secret-config paths
such as `application.env`, actual credential values in the fixed YAML files,
and unsupported secret configuration rather than redacting and claiming exact
bytes. Original YAML node pairs are checked before loader duplicate/merge
behavior could hide credentials. Errors must not echo secret values.

Error codes covered by RED: `inventory_unsupported`,
`inventory_self_uncovered`, `inventory_changed`, `source_missing`,
`source_invalid`, `symlink`, `secret_config`, `archive_integrity`,
`capsule_invalid`, `python_ast_changed`, `non_python_changed`, `non_local_store`,
and `source_changed` for a source changing during capture/verification.

**Fixture boundary.** `archive_case` copies the full actual native inventory
and checks its aggregate against the real pipeline hash before/after copying.
It loads the current proposed implementation at the copied installation path,
not from an archive, and points the native pipeline's `__file__` at that same
copied installation. It replaces no inventory resolver, hash function or
verification logic.
No positive fixture edits the pipeline list. Mutations occur only in isolated
copies for rejection/security tests; there is no grade, current/promoted row,
epoch authority, paid record, database, network, or model fixture.

Main RED target (worker does not run it):

```text
tests/unit/full_flow_issuer/test_review_issuer_archive.py
```

Primary cases: complete inventory/original bytes, immutable/idempotent capture,
read-only zero-authority verification, comment-only upgrade with distinct full
hashes, all AST/non-Python/path changes, unsupported inventory/hash behavior,
self-coverage, missing/invalid sources, secret config, source/archive symlinks,
missing/changed/extra archive material, self-consistently readdressed hostile
manifests, caller summaries/authority, local-reference confinement, no repair,
no archived-code execution, and non-local-store rejection.

Unknowns intentionally left to later scope: native source issuer emission,
paid-input binding, complete transitive dependency coverage, shared consumer
integration, issuer report/raw reconstruction, actual post-F0 issuance, and
E05b evidence. These contracts grant none of them.
