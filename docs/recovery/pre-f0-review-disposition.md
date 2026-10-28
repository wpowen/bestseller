# Pre-F0 review disposition — implementation checkpoint

This is a development checkpoint, not CP1 acceptance or a production release.
Pre-F0 paid sources are preserved and classified `pre_f0_source_unprovable`.
The normal resume preparation appends a separate supersession receipt and a
deterministic successor pointer. It does not rewrite the original cycle,
attempts, once receipts, report, task, financial rows or Unknown outcome.
The old active pointer is retained separately for audit. Ordinary review code
creates the successor cycle and retains normal financial admission.

`review_source_ordering` centralizes disposition and archived-task checks.
It is separate from the read-only `recorded_review_sources` codec because its
native preparation changes chapter control metadata. Task consumers reuse its
native original-receipt reconstruction: self-consistent JSON hashes alone
cannot remove a task from readiness or alter its history. Existing pending
tasks without a valid disposition retain their existing behavior.

Unknown/unsettled financial scopes remain blocked, with no successor or new
attempt. A source adopted after preparation cannot start a fresh review;
an already existing successor still undergoes normal complete preflight.
Reader revision requests cannot act on another chapter's task, or mutate an
archived original through humanize/polish/regenerate paths.

Evidence on 2026-10-28:

- S01 index/seq classification: same `pre_f0_source_unprovable`, original five
  paid inputs verified, no DB writes/provider calls.
- Earlier candidate native prepare/preflight rollback and Unknown negative:
  old cycle/task/financial rows unchanged. Subsequent review guards mean this
  is not full native acceptance of the current checkpoint.
- Original five-input receipt reconstruction and actual adopted-prefix
  rejection rechecked read-only; all protected table fingerprints unchanged.
- Current local regression: 194 passed. Fake archive positives were withdrawn
  and replaced with mandatory rejection cases. Existing fake-session revision
  tests substitute only their no-archive boundary; they are not custody proof.

Remaining: F0 deterministic readers/E21; source ordering epoch emitted only
after its producer contract is implemented; recorded-view integration at all
historical proof boundaries; shared post-F0 issuer view/E05b; E22; complete
scripted normal review and CP1. No production continuation, deployment or paid
call is authorized by this checkpoint. The withdrawn applicability prototype
is neither imported nor committed.
