# Cross-client picker parity implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development and test-driven-development. Check every step before marking complete.

**Goal:** Mac desktop and mobile Hermex consume the same fleet catalog and synchronize picker presentation preferences without erasing existing user state.
**Architecture:** Hermes Agent owns inventory and a non-secret atomic versioned preference store. WebUI adapts its /api/models response to this inventory (keep existing response compatibility), and both clients read/write the same preference contract. Model defaults remain profile-local; provider/model catalog and presentation preferences are shared for this local installation, independent of origin/device.
**Tech Stack:** Python backend, TypeScript desktop stores, JavaScript WebUI, pytest/frontend targeted tests.
**Spec:** /Users/user/.hermes/profiles/techrecon/cache/scratch/hermex-picker-diagnosis.md and approved user request to implement, commit and deploy recommended shared inventory/config/default and preference synchronization.

## Global Constraints
- No credentials in source/receipts. Preserve profile-local defaults, sessions, auth, tools and other settings. No provider generation or catalog membership changes required.
- Shared preferences use stable literal provider::model identity. Preserve orphan IDs; catalog disappearance is not preference deletion.
- Import existing desktop favorites/visibility/custom rows once only if the server state is uninitialized; never overwrite initialized remote state with empty client defaults. Preserve a recovery copy of existing client values.
- Atomic persisted preference writes and revision conflict rejection prevent concurrent device lost updates. Reject malformed payloads and unknown fields, cap lengths/body size. Existing authentication applies to REST and RPC handlers.
- Explicit profile scope for new-session default reads/writes. Effective fleet projection read only; persist model defaults to that profile via existing supported writer, not serialize effective provider map into profile YAML.
- Cache signatures track shared catalog changes. No expensive provider probing on normal picker open. Health overlay must remain post-catalog; never persist it as preference.
- Work on feature branches in BOTH clean repositories. Parent controls commits, merge, push and runtime restarts. Focused tests only; independent review required. No unrelated repository changes.

### Task 1: shared backend and both client wiring (one atomic feature)
**Files:** Agent: new hermes_cli/picker_preferences.py; inventory.py and tui_gateway/methods_complete.py and RPC contracts; desktop store/{favorite-models,model-visibility,custom-models}.ts plus shared sync module and app bootstrap. WebUI: new api/picker_bridge.py (keep config.py changes small); api/config.py, routes.py, models.py as needed; static/ui.js or focused shared module. Add focused new tests alongside existing related tests.
**Interfaces:** versioned preference shape {version:1, revision:int, initialized:bool, favorites:string[], visibility:<existing desktop representation normalized>, custom_models:<existing desktop rows normalized>}. Worker must inspect current representations before finalizing schemas and record exact types in plan addendum. Python get/update APIs are the single implementation used by REST and RPC. update takes expected_revision and rejects mismatch with 409/equivalent RPC conflict; explicit intent can clear lists, missing fields cannot erase lists.
- [ ] Read current implementations and contributor instructions; write exact schema/route addendum before production edits.
- [ ] RED then GREEN for shared store: read uninitialized, import-once populated preference, repeat import no-op, explicit clear, concurrent stale revision rejection, orphan preservation, malformed input, atomic storage.
- [ ] RED then GREEN for mobile inventory: apply fleet overlay to raw profile config; adapt build_model_options_payload(load_picker_context()) to existing groups shape without dropping exact catalog pairs. Request profile cookie must control inventory/default scope, not global process state. Compare canonical mobile/static Mac catalog pairs under same scope and require named Z.ai/Contabo entries.
- [ ] RED then GREEN for shared-only catalog signature invalidating both mobile config and models cache; profile YAML untouched by catalog read.
- [ ] RED then GREEN for defaults: mobile selected provider/default model saved to intended raw profile; next chat sees same effective defaults Mac sees. Other profiles and catalog unchanged.
- [ ] RED then GREEN RPC and REST preference get/update handlers, use existing auth and runtime context; bounded validation and conflict response. Do not bypass auth for deployment probes.
- [ ] RED then GREEN desktop hydration and write-through using same endpoint via RPC; existing local state backed up and imported once; no initial load race. Favorites, custom rows, visibility reflect server changes on next open/focus without feedback-loop overwrites.
- [ ] RED then GREEN mobile equivalent controls and shared sync: favorites/visibility/custom rows equivalent to desktop; profile-qualified selected model storage; stable provider::model key conversion. Existing session choice and defaults remain profile-local, not forced by shared preferences.
- [ ] Run targeted Python tests and frontend type/build tests. Produce full commands/results including RED evidence, exact modified files and deployment verification recipe. Record any acceptance gaps honestly; do not claim complete for only catalog portion.

### Task 2: independent review and delivery (parent)
- [ ] Review diff and worker evidence; dispatch independent spec/security/race reviewer. Fix blockers with regressions before commit.
- [ ] Run acceptance tests independently; verify preference migration/round-trip read-write-read across both APIs and profiles; restore exact test preferences afterward. Verify catalog real inventory parity, not just overlay.
- [ ] Commit both repositories using Bud-Fox noreply identity; merge canonical local branches, push to authorized forks and verify remote SHA. If WebUI no owned fork exists create via gh supported workflow (no upstream direct push).
- [ ] Build desktop supported hermes desktop --build-only --force-build and refresh affected runtimes via drain-aware supported methods; webui launchctl kickstart existing supervisor. Verify live imports, health and changed APIs on actual serving processes.
- [ ] Update hermes-model-picker-ops inaccurate claim that WebUI already used common endpoint. Save evidence and concise verdict with exact test count/SHAs/live caveats. Do not declare whole task complete if real client hydration/round-trip or build blocked.
