# Stable Picker Overlay + NVIDIA Slow-Model Routing Implementation Plan

> Execute with `subagent-driven-development`; each production change follows RED→GREEN→REFACTOR.

**Spec:** `docs/superpowers/plans/2026-09-30-stable-picker-nvidia-routing-spec.md`

## Global constraints

- Hermes repo: `/Users/user/.hermes/hermes-agent`; tests via `scripts/run_tests.sh` and Desktop Vitest.
- Router repo: `/Users/user/contabo-router`; tests via `/Users/user/.hermes/hermes-agent/.venv/bin/python app/run_tests.py`.
- Implement in isolated worktrees/branches. Do not modify unrelated untracked router artifacts.
- Preserve readiness glyphs and strict vendor-local routing.
- Router production deployment only from merged main checkout via `./deploy_router.sh`, after drift gate and review.

### Task 1 — Picker stable snapshot and live overlay

**Files:** Desktop model-options/query/menu files and colocated Vitest tests.

- [ ] Add failing behavior tests proving: health rerender while open preserves row order and DOM row identity; focus/scroll selection remain; catalog query does not refetch after one minute/focus.
- [ ] Extract a pure stable-order reconciliation helper keyed by `provider::model` if required.
- [ ] Configure catalog query as event-invalidated (`staleTime: Infinity`, no focus polling).
- [ ] While open apply readiness/glyph fields in place; stage resort until close/select.
- [ ] Run focused Vitest then relevant Desktop suite.
- [ ] Commit and independent task review.

### Task 2 — Router model policy and bounded stall sweep

**Files:** `app/gateway.py`, `app/test_router.py` or topical test module.

- [ ] Add failing tests for GLM 90/120 and Kimi 180/240 policies, other NVIDIA default unchanged.
- [ ] Add failing async dispatch tests proving pre-first-byte timeout attempts at most two distinct credentials, does not globally fail keys, stops on total budget, and returns explicit `MODEL_STALL` diagnostic.
- [ ] Implement table-driven slow-model policies and request-local deadline/attempt cap.
- [ ] Preserve normal handling for auth, billing, 429 and non-stall failures.
- [ ] Run focused tests, full router suite, and reverse-mutation proof.
- [ ] Commit and independent task review.

### Task 3 — Kimi wire adapter

- [ ] Add failing tests for non-mutating payload transformation, reasoning effort, preserved assistant reasoning/tool-call history, stream/non-stream behavior.
- [ ] Implement the narrow Kimi adapter before each upstream request.
- [ ] Run focused and full suite; commit and review.

### Task 4 — Whole-branch review, merge and live validation

- [ ] Whole-branch independent review for each repo; one bounded fix wave if needed.
- [ ] Fast-forward merge feature branches into owning checkouts.
- [ ] Re-run acceptance suites after merge.
- [ ] Rebuild Hermes Desktop and restart application/backend.
- [ ] Router drift gate, backup, `./deploy_router.sh`, hash/readiness verification.
- [ ] Live GLM and Kimi stream canaries; verify attempt counts and key states from logs.
- [ ] Keep picker open through two health ticks and verify stable rows with changing glyph overlay.
