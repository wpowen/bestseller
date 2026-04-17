# Self Memory

Mistake: Missing mandatory memory files before task execution.
Wrong: Assume `.remember/memory/self.md` and `.remember/memory/project.md` always exist and proceed without fallback initialization.
Correct:
- Attempt to read both files first.
- If files do not exist, create baseline files immediately.
- Continue task execution while following user-provided rules in current conversation.

---

Mistake: `_LLMCaller._call` had no retry logic for transient errors (Timeout, Connection, RateLimit, APIError).
Wrong: Single-shot LLM call that raises raw exceptions or converts None to "None" string.
Correct:
- Wrap litellm completion calls with retry logic (3 attempts, exponential backoff 5/15/45s).
- Check for `content is None` explicitly and raise descriptive RuntimeError.
- Distinguish retryable vs non-retryable errors and only retry transient ones.
- Provide actionable error messages (model name, prompt length, max_tokens) to aid debugging.

---

Mistake: `generate_world_snapshot` / `generate_arc_summary` blindly return `_parse_json(raw)` without type checking.
Wrong: LLM may return a JSON array instead of object; `_parse_json` returns `list`, which is stored in state and later crashes when `.get()` is called on it.
Correct:
- Add `_ensure_snapshot_dict()` / `_ensure_summary_dict()` normalizer after `_parse_json`.
- If result is a list of dicts, merge them into a single dict.
- Also sanitize `prev_snapshot` input in case corrupted data was already persisted to state.
- In pipeline, sanitize `world_snapshots` loaded from state to ensure all elements are dicts.

---

Mistake: Assume `python` command exists in all environments when verifying generated content metrics.
Wrong: Run `python` directly and fail on systems where only `python3` is installed.
Correct:
- Prefer `python3` for verification scripts in this repository environment.
- If command fails, immediately retry with `python3` and continue validation.
