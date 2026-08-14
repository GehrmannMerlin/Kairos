# Production Reality Closure Plan

> **Execution rule:** implement one closure module per reviewed branch, using systematic debugging for observed failures, test-driven development for every correction, and verification-before-completion before merge/release. Do not combine unrelated P1/P2 work with the current P0 incident.

**Goal:** close the evidence-backed gaps in `docs/audits/production-reality-audit-2026-08-14.md` in dependency and risk order, while preserving owner isolation, immutable versions, real external calls and fail-closed behavior.

**Architecture:** keep the existing Vue → FastAPI → application/domain → Temporal/activity → provider/network/PostgreSQL/object-storage boundaries. Repairs converge duplicate fact resolution and fill missing execution contracts; they do not replace failures with fixtures or frontend success states.

**Tech stack:** Vue 3/TypeScript/Vitest/Playwright; FastAPI/Pydantic/SQLAlchemy/Pydantic AI/httpx/Temporal; PostgreSQL/MinIO; Docker/GHCR/CI.

---

## Closure-01 — Model catalog and real Goal Understanding

- Scope: current incident only: provider model discovery, select-only UI, test/inference parity, HTTP error classification, legacy invalid-config recovery, and real release smoke.
- Affected modules: M-03, M-06, M-18.
- Findings: `AUDIT-PROVIDER-001`, the model-specific portion of `AUDIT-RELEASE-001`.
- Root causes: user-entered model fact; probe checked authentication but not selected-model membership; inference classified HTTP 400 as network failure; release smoke hardcoded an obsolete model.
- Files: `backend/app/providers/**`, `backend/app/api/routes/providers.py`, `frontend/src/features/providers/**`, `backend/tests/providers/**`, `backend/tests/api/test_understand.py`, `infra/scripts/_m18_production_smoke.py`.
- Contracts: Provider registry remains the base-URL fact source; Vault is the only execution credential reader; an existing credential is bound to its original Provider; catalog returns IDs/status only; UI saves only a returned ID; Tavily is not invoked in Goal Understanding.
- Tests: adapter catalog parsing; owner-scoped transient/saved credential catalog; same-Provider credential binding; model membership; shared transport; 400/401/404/429 mapping; typed GoalUnderstandingResult; failure preserves one Task/message; cross-user denial; log/history secret scans; frontend loading/error/stale-request/legacy-invalid states.
- Staging smoke: login → real DeepSeek catalog → select `deepseek-v4-flash` returned by provider → save/test → new Task → real natural-language Goal Understanding → CollectionSpecDraft rendered; verify no Tavily request before search stage.
- Done criteria: scoped backend/frontend gates pass; immutable Staging images pass the real smoke; the same digests are promoted to Production; Task 5-style input no longer reports `NETWORK_ERROR`; catalog options are visible at `app.kairos.ac.cn/models`.

## Closure-02 — Plan persistence and Temporal start semantics

- Scope: make plan generation/persistence/start an explicit, idempotent command boundary; remove write-command `_NoopStarter` fallback.
- Affected modules: M-07.
- Findings: `AUDIT-PLAN-001`.
- Root causes: one broad exception handler spans persistence and external workflow startup; fallback repeats persistence and hides startup failure.
- Files: `backend/app/api/routes/plans.py`, Plan application service/repository, workflow starter, plan API schemas/tests.
- Contracts: persist at most once per fingerprint/version; distinguish plan-generated from execution-started; startup failure is typed and retryable; query services may still use a no-op starter because they never execute commands.
- Tests: Temporal unavailable; timeout; first persist succeeds/start fails; repeated retry; concurrent same-fingerprint command; no duplicate PlanVersion/Run; frontend renders retryable start failure.
- Staging smoke: pause Temporal worker/client availability at the controlled boundary, generate plan, confirm one version and typed state, restore dependency, retry exactly once, observe one Run/workflow.
- Done criteria: no broad write-path fallback; no duplicate persistence; plan/run state is truthful in API/UI; failure has a redacted trace id.

## Closure-03 — Artifact node execution closure

- Scope: ensure every validator-admitted Plan node has a real worker executor, beginning with `generate_artifact`.
- Affected modules: M-08, M-15.
- Findings: `AUDIT-ARTIFACT-001`.
- Root causes: generator/registry admit `GENERATE_ARTIFACT`, while worker installation omits an artifact executor.
- Files: `backend/app/plan/nodes.py`, `backend/app/agents/plan_generator.py`, new or existing artifact executor/service adapter, `backend/app/worker.py`, plan execution tests, artifact/export tests.
- Contracts: executor calls the existing passed-record export service; output is content-addressed in object storage; retries use request fingerprint/idempotency; no empty/fixed CSV; failed generation is a typed node failure.
- Tests: node-registry completeness; PASSED-only export; zero-row truthful artifact; duplicate activity retry; storage error; owner isolation; downloadable content hash.
- Staging smoke: directed tiny task → validate records → `generate_artifact` activity → READY CSV → download and compare row count/hash → Run completes.
- Done criteria: no admitted Production node resolves `NODE_EXECUTOR_UNAVAILABLE`; repeated activity execution produces one READY artifact; a real Run reaches terminal completion.

## Closure-04 — Frozen extraction model resolution

- Scope: remove current-default fallback after Plan freeze and make missing immutable references explicit.
- Affected modules: M-11.
- Findings: `AUDIT-EXTRACTION-001`.
- Root causes: optional Plan refs and broad resolver exception handling permit mutable default resolution or silent “no model”.
- Files: extraction resolver/agent/executor, Plan validator/persistence, credential/model repositories, Temporal payload/redaction tests.
- Contracts: semantic nodes require `model_config_id + version`; credential reference/version is resolved owner-safely at activity execution; secrets never enter Plan or Temporal history; configuration rotation does not change existing Runs.
- Tests: rotate default during Run; revoked credential; missing version; cross-user config; typed error; Temporal history payload scan; evidence records exact non-secret model ref.
- Staging smoke: freeze Plan with model vN, create vN+1/default switch, execute semantic fallback, prove vN was used from audit metadata.
- Done criteria: no mutable-default lookup for frozen Runs; failures are typed; evidence is reproducible without secret material.

## Closure-05 — Current real external golden path

- Scope: obtain current runtime proof for SourceSearch, frontier/robots, static fetch, browser escalation, extraction, validation, evidence and quality.
- Affected modules: M-09, M-10, M-11, M-12, M-14, M-18.
- Findings: `AUDIT-E2E-001`, remaining `AUDIT-RELEASE-001`.
- Root causes: deterministic fixtures were treated as stronger evidence than they provide; dynamic-browser external proof was deferred; no current completed Production Run exists.
- Files: staging acceptance scripts, release smoke, allowlisted golden fixtures/expectations (URLs only, no copied business result), observability assertions, deployment records.
- Contracts: Goal Understanding proof is reported separately from Tavily/search proof; search is invoked only for exploratory/hybrid tasks; real pages create real snapshots/evidence; browser escalation is evidence-driven; expected output is asserted without hardcoded returned business data.
- Tests: provider/search transport regressions without real keys; static and browser adapter contract tests; real Staging golden task; bounded Production smoke only after Staging.
- Staging smoke: one small government/allowlisted directed task and one minimal exploratory task; assert candidate source, robots decision, snapshot, record partition, field evidence, quality metrics and CSV.
- Done criteria: at least one current immutable Staging Run completes through CSV; Production bounded smoke completes or any later-stage failure is recorded as a new, separate finding; retained trace is secret-free.

## Closure-06 — Reliability and observability evidence separation

- Scope: keep deterministic M-16 capacity tests but label them synthetic; attach safe trace/log evidence to real-path release gates.
- Affected modules: M-16, M-17, M-18.
- Findings: `AUDIT-RELIABILITY-001`, `AUDIT-OBSERVABILITY-001`.
- Root causes: synthetic load and external integration proof were conflated; successful full-path trace evidence is absent.
- Files: M-16 harness/report, OTEL/logging configuration, release scripts/records, redaction tests.
- Contracts: synthetic tests never claim external correctness; traces contain only allowlisted metadata; log scanning fails on secrets; release records bind evidence to commit/tag/digests.
- Tests: capacity/concurrency/idempotency harness; trace correlation; negative secret patterns; provider/search exception taxonomy; readiness/rollback drill.
- Staging smoke: run synthetic A-Lite capacity gate, then separately reference Closure-05 trace IDs and secret-scan result.
- Done criteria: reports label synthetic versus real evidence; a current completed external Run has correlated safe traces; rollback evidence identifies exact immutable digests.

## Dependency order and release policy

1. Closure-01 ships first because it is the active P0 incident.
2. Closure-02 makes execution start state truthful before more full-path claims.
3. Closure-03 permits standard Plans to reach completion.
4. Closure-04 makes semantic results reproducible across rotations.
5. Closure-05 proves the repaired full path with current external systems.
6. Closure-06 hardens evidence/reporting after the real golden path exists.

Each closure stops at its own done criteria. P2 work never delays a P0 fix unless verification shows it creates a security, ownership, secret or data-integrity risk for that release.
