# Phase A Runtime Methodology Verification

Date: 2026-05-27
Project: `exorcist-detective-1778051012`

## Result

PASS.

The mocked scene dry-run assembled a fresh `scene_writer` prompt and wrote a
full trace:

`output/exorcist-detective-1778051012/traces/scene-prompt-ch0010-s01-20260527T072054241111Z.json`

The dry-run rolled back database writes after prompt assembly.

## Commands

```bash
.venv/bin/python scripts/verify_methodology_runtime.py
.venv/bin/python scripts/dry_run_one_scene.py --slug exorcist-detective-1778051012 --chapter 10 --scene 1
.venv/bin/python scripts/verify_methodology_in_latest_trace.py --slug exorcist-detective-1778051012
```

## Evidence

`verify_methodology_runtime.py`:

```text
OK PROSE_SCENE+suspense-mystery+ch42: 2910 chars, 1158 tokens, sources=('prompt_packs/suspense-mystery.yaml', 'writing_methodology.yaml', 'prose_style_anchors.yaml')
OK CONCEPTION+suspense-mystery: 1109 chars, 439 tokens, sources=('prompt_packs/suspense-mystery.yaml',)
OK OUTLINE_BOOK+suspense-mystery: 1958 chars, 778 tokens, sources=('prompt_packs/suspense-mystery.yaml', 'writing_methodology.yaml')
OK PROSE_SCENE+None_pack: 3461 chars, 1379 tokens, sources=('writing_methodology.yaml',)
```

`verify_methodology_in_latest_trace.py`:

```text
checking: output/exorcist-detective-1778051012/traces/scene-prompt-ch0010-s01-20260527T072054241111Z.json
mode=full system_chars=5239 user_chars=36299
optional present: ['【prompt_pack.scene_writer】']
OK all required markers present
```

## Notes

The trace proves `【题材方法论·正文场景】` reaches the user prompt. System prompt
size remained `5239` chars for this dry-run.
