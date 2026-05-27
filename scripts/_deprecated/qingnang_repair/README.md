# DEPRECATED

These scripts encode project-specific user feedback as hardcoded DB mutations.
That pattern is deprecated because it bypasses the methodology, prompt-pack, and
LLM-judge feedback loop.

Going forward, user feedback should flow into:

- `config/writing_methodology.yaml`
- `config/prompt_packs/<genre>.yaml`
- LLM judge prompts and structured rewrite plans

Do not add new `repair_qingnang_*.py` scripts in the active `scripts/` folder.
