# Fleet delegation implementation plan

> **For agentic workers:** Use executing-plans; implement and verify each task before integration.

**Goal:** Every discovered profile, including default and new profiles, lets agents select exact catalog provider/model pairs under one shared exclusion policy. Never silently substitute a requested reviewer.

**Architecture:** Add an optional delegation policy to the existing version-1 fleet catalog. Project its routes and empty default pins into effective profile config; retain profile-local limits. Static membership is distinct from runtime authorization/readiness. Exclude contabo-anthropic, anthropic, contabo-openrouter from shared routes without deleting picker entries. Dynamic delegate schema lists actual permitted pairs. Child execution ledger remains ground truth; notifications must not label overridden children with default pins.

**Tech Stack:** Python, existing YAML/config cache and runtime resolver, pytest.

**Spec:** User request in session 20260912_143357_a6451a: autonomous model/provider selection across all Hermes profiles; do not recommend expired Anthropic; implement and deploy after readiness.

## Global constraints
- Discover root plus named profiles; no hard-coded fleet size.
- Keep credentials profile-bound; do not copy secrets.
- No full-directory pytest; only focused files, --basetemp under profile scratch (bounded reusable directory).
- No generation or paid escalation beyond existing authorized configured routes; bounded canary only.
- Preserve explicit exclusions; unknown health is unknown, not healthy.
- Preserve literal provider/model IDs; no aliases to arbitrary custom/vendor providers.

## Task 1: Shared policy projection and RED/GREEN
Files: hermes_cli/fleet_catalog.py; tests/hermes_cli/test_fleet_delegation.py (new).
Interface: version-1 catalog optional delegation mapping {excluded_providers: list[str]}; routes generated from catalog providers/models minus exclusions, model/provider/base_url/api_key/api_mode default pins cleared in effective config only. Existing catalogs without policy retain compatibility.
- [ ] Write focused tests for catalog-derived glm via zai-coding-plan, exclusions, default and named config equality, preserved limits, new profile inheritance, existing catalog compatibility and invalid policy fail-closed.
- [ ] Run test before implementation, require failure.
- [ ] Implement validation and projection using existing apply_fleet_catalog; verify cache invalidation includes catalog signature (already present; check).
- [ ] Run focused tests, require pass.

## Task 2: Correct route validation/discovery and notification contract
Files: tools/delegate_tool_config.py, tools/delegate_tool.py, tools/delegate_tool_dispatch.py; new focused tests/tools/test_fleet_delegation_routes.py.
Interfaces: _resolve_task_route(task,cfg,parent) uses cfg.routes (no second ambient profile read), fails closed on missing/empty/malformed models; route credentials resolved through runtime resolver. No model/provider pair => parent inherit when shared policy active. Preflight resolve all task routes before child construction, no leaking partially constructed children. Dynamic schema lists authorized exact pairs; preserve KPI choice with parent provider as preferred not mandatory. Mixed batch metadata must report actual child routes, not default pin.
- [ ] Add RED tests for cfg ownership, empty list rejection, dynamic pairs, per-task override metadata, all-route preflight before construction.
- [ ] Implement minimal changes and GREEN focused tests.
- [ ] No claims of health from successful credential resolution.

## Task 3: Activation, independent review and fleet verification
Files: canonical catalog.shared.yaml only via backed-up atomic change; docs and topical skill reference.
- [ ] Review diff and run focused tests; inspect potential pre-existing failures without unrelated changes.
- [ ] Snapshot canonical catalog bytes and mode before mutation; activate shared delegation exclusions while preserving providers.
- [ ] Read effective config through each list_profiles() scope and assert identical routes, no default pins, exact zai-coding-plan/glm-5.3 membership, exclusions and schema parity; include discovered default.
- [ ] Inspect live readiness without persisting it; resolve all authorized routes per profile and report unavailable credentials separately.
- [ ] Commit, merge main, push fork, verify remote SHA.
- [ ] Discover actual desktop serve and gateway processes, replace stale runtimes via supported lifecycle. Do not manually overwrite immutable PM workspaces: determine actual imported source and deployment path first.
- [ ] Run bounded real per-task child with requested glm-5.3 via zai-coding-plan, verify result plus session_model_usage model/provider/endpoint. If live auth blocks it, report blocker accurately and do not substitute a different model.
- [ ] Update skill: shared policy, all discovered profiles, ledger evidence, no unsupported health/grounding claims; record autonomous merge/deploy rule.
