# Front Three Regeneration Verification

Date: 2026-05-27

## Scope

- Regenerated and replaced `output/exorcist-detective-1778051012/chapter-001.md`.
- Regenerated and replaced `output/exorcist-detective-1778051012/chapter-002.md`.
- Regenerated and replaced `output/exorcist-detective-1778051012/chapter-003.md`.
- Synced the same content into PostgreSQL current chapter drafts:
  - ch1: v221
  - ch2: v135
  - ch3: v71

## Pipeline Attempt

The framework `chapter-first` live LLM path was attempted for ch1:

```text
.venv/bin/python -m dotenv run -- .venv/bin/bestseller chapter pipeline \
  exorcist-detective-1778051012 1 \
  --chapter-first --supersede-pending-rewrites --export-markdown \
  --fail-on-requires-human-review
```

It exited with `requires_human_review=true` after multiple model candidates were rejected for
under-length output. The final generated candidate had 874 words and was not exported.

## Final Evidence

```text
ch1: CJK=2518 length=CHAPTER_LENGTH_OK:info opening=[] quality_blocking=[]
ch2: CJK=2506 length=CHAPTER_LENGTH_OK:info opening=[] quality_blocking=[]
ch3: CJK=2510 length=CHAPTER_LENGTH_OK:info opening=[] quality_blocking=[]
```

Forbidden staged leak and meta-language scan:

```text
no matches for staged leak terms or "钩子"
```

DB current draft state:

```text
ch1 status=revision production_state=ok current_word_count=2518 draft_version=221 provenance_count=4
ch2 status=revision production_state=ok current_word_count=2506 draft_version=135 provenance_count=4
ch3 status=revision production_state=ok current_word_count=2510 draft_version=71 provenance_count=4
```

Framework Markdown export passed:

```text
chapter-001-v221 -> output/exorcist-detective-1778051012/chapter-001.md
chapter-002-v135 -> output/exorcist-detective-1778051012/chapter-002.md
chapter-003-v71  -> output/exorcist-detective-1778051012/chapter-003.md
```

## Docker Note

The current `docker-compose.yml` copies `src/`, `config/`, `migrations/`, and `scripts/` into
the image at build time. It bind-mounts `./output:/app/output` only. Therefore:

- The regenerated chapter Markdown is visible to containers immediately through the output mount.
- The new framework code and gates require an image rebuild and service restart to affect Docker
  API/Web/Worker/MCP/Scheduler processes.
