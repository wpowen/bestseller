# Narrative Contract Gates

Date: 2026-05-08

## Goal

Prevent character identity drift, timeline looseness, and repeated thin scene patterns before prose generation. The workflow must fail closed when upstream planning data is incomplete, instead of relying on repeated human or LLM review after chapters are written.

## Design

1. Foundation identity contract
   - CastSpec is the identity source of truth.
   - Every named cast character that enters the story must have a locked gender and effective Chinese/English pronoun set.
   - The materializer blocks incomplete CastSpec content before it can be persisted.
   - A compact identity manifest is stored on the project for downstream plan gates.

2. Chapter outline contract
   - Chapter plans may only reference known character names or aliases from the identity manifest.
   - Every scene must carry an explicit time label, participant list, and story purpose before it becomes a `SceneCard`.
   - The materializer blocks invalid outline batches before chapter or scene rows are written.

3. Pre-draft scene contract
   - The scene pipeline re-validates the concrete `SceneCard` against the identity registry before drafting.
   - If the card references an unknown participant, a participant with unresolved gender, or misses time/purpose data, drafting is blocked.

4. Existing write gates remain as fallback
   - Post-draft identity validation, contradiction checks, plan-richness checks, L2 bible gate, Phase D time gate, and plan fingerprint checks still run.
   - These gates should catch regression paths, not carry the primary burden.

## Implementation Checklist

- [x] Add reusable narrative contract report and validators.
- [x] Store a project-level identity manifest during story-bible materialization.
- [x] Block incomplete CastSpec identity locks before persistence.
- [x] Block chapter outline batches with unknown participants or missing scene time/purpose.
- [x] Block invalid scene cards before writer LLM calls.
- [x] Add focused unit tests for each gate and run targeted verification.
