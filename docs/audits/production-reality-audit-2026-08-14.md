# Kairos Production Reality Audit — 2026-08-14

## Audit identity and boundaries

- Task type: Production incident investigation + production-reachability audit + closure planning.
- Production release inspected: `v0.1.2-c61f873eae8c` (`c61f873eae8c`).
- Production images: web `sha256:9c451f6cb0304807b0d39b2f16167bc0169740f7263ce94955856940929221f4`; api `sha256:26f4cbc6db5edb188ba15f32d49eab3518b9ae4250aaa4cb51ad134fb14ff3b7`; worker `sha256:83e2a12c7e75fcf14344320b3d21b11808230d17445715891c46c13a905961fe`.
- Migration: `0014`.
- Scope: user-visible paths from frontend to API, application/domain, Temporal/activity, provider/network/database/object storage, and back to the frontend.
- Non-negotiable boundaries: no plaintext secrets or authorization headers in evidence; no cross-user access; no fake fallback; Tavily is not part of Goal Understanding; no server-side source edits or image builds.

The audit compares the repository at `7fe52c2` plus the incident-fix branch against the live Production release. Documentation marked `DONE` is treated as historical intent, not runtime proof.

## Classification standard

| Classification | Meaning in this audit |
| --- | --- |
| REAL | Production-reachable path calls its real database, workflow, provider, network or storage dependency and returns runtime-derived data. |
| TEST_ONLY_MOCK | Test fixture or injected double that cannot enter Production runtime. Accepted and not a defect. |
| DEV_ONLY_FAKE | Explicit non-production facility with verified Production isolation. |
| PROD_MOCK | Production can return fixed or simulated business results. |
| STUB_OR_NOOP | A callable contract exists but does no business work, returns fixed success/empty data, or has no implementation. |
| HALF_WIRED | Adjacent layers exist but the user path does not traverse all of them. |
| PLACEHOLDER_UI | Explicit empty state is acceptable; an action presented as working but not wired is a defect. |
| SYNTHETIC_ONLY | Only fixture/synthetic proof exists for the claimed capability. |
| SILENT_FAKE_FALLBACK | A real failure is silently replaced with fake success/data. Forbidden in Production. |
| ENVIRONMENT_MISMATCH | Local/Staging/Production resolve different paths or facts. |
| UNVERIFIED | Implementation looks substantive but there is no current external/runtime proof. |
| DEAD_CODE | Not reachable from the current runtime. |

## Method and evidence limits

The static candidate scan covered the required mock/fake/stub/placeholder/TODO/no-op/empty-return/hardcoded/environment/fallback/exception patterns across Vue/TypeScript, FastAPI, providers, agents, workflow, crawling, extraction, validation, storage and release tooling. Every reported defect below was then traced to a user entry and runtime call path; grep hits in tests alone were not reported as Production defects.

Read-only Production checks established:

- the API and worker run with `KAIROS_ENV=production` and no fixture-mode setting;
- the immutable release identity above is running in all three application containers;
- Production currently contains 5 Tasks, 3 PlanVersions, 3 Runs, 3 PageSnapshots, 3 Records, 6 FieldEvidence rows and 2 Artifacts;
- all 3 Runs are `partially_completed`; there is no completed Production Run;
- Task states are 1 `DRAFT`, 1 `QUEUED`, and 3 `PARTIALLY_COMPLETED`.

These counts prove that the persistence surfaces are real, but they do not prove a complete external collection path. No private prompt, page content, record payload or secret was read for this audit.

## Reality coverage summary

Status semantics: `REAL` is currently evidenced; `GAP` has a Production-reachable break; `UNVERIFIED` has substantive code but no current real-path proof; `SYNTHETIC_ONLY` is supported only by fixtures/harnesses.

| Module | Status | User entry / expected real path | Runtime or finding evidence |
| --- | --- | --- | --- |
| M-01 | REAL | repository, CI/release contracts, container entrypoints | Immutable GHCR release is running; local/staging fixture gates are explicit. Current full CI must still pass for the fix. |
| M-02 | REAL | register/login/session/task ownership | Production Task 5 and messages are owner-scoped and persisted; ownership/security tests exist. No cross-user bypass found. |
| M-03 | GAP | `/models` → config/version/credential/vault/provider | Production accepted an arbitrary model name and probe did not validate it. `AUDIT-PROVIDER-001`. |
| M-04 | REAL | Task/Spec/Plan/Run/Record/Evidence persistence | Production rows exist in PostgreSQL with explicit `user_id`; state/event contracts are substantive. |
| M-05 | REAL | `/app`, `/tasks`, `/templates`, `/models`, task workspaces | UI reads backend APIs; no Production static business dataset or fake progress path found. Explicit empty states are not defects. |
| M-06 | GAP | `/tasks/:id/chat` → Goal Understanding → real model | Live Task 5 failed before typed result because invalid model ID was labelled `NETWORK_ERROR`. `AUDIT-PROVIDER-001`. |
| M-07 | GAP | plan generation → persist → Temporal start | A broad exception path returns a persisted plan without a workflow and may persist twice. `AUDIT-PLAN-001`. |
| M-08 | GAP | typed nodes → worker executors → Run completion | `generate_artifact` is a generated/registered node type but has no Production executor. `AUDIT-ARTIFACT-001`. |
| M-09 | UNVERIFIED | Tavily → SourceSearch → real candidates/frontier | Real adapter/executor code exists and is separate from Goal Understanding; no current Production real-search completion proof. `AUDIT-E2E-001`. |
| M-10 | UNVERIFIED | robots/frontier → HTTP/Scrapy/Playwright → snapshot | Real executors and snapshot persistence exist; current dynamic-browser proof was deferred and no completed Production Run exists. `AUDIT-E2E-001`. |
| M-11 | GAP | snapshot → rule/JSON-LD → real LLM fallback → evidence | Real inference is implemented, but missing frozen Plan refs can fall back to the user's current default and resolver exceptions are collapsed. `AUDIT-EXTRACTION-001`. |
| M-12 | UNVERIFIED | records → dedupe/conflict/review partitions | Real services and persisted Production records exist; only synthetic/current partial-run evidence is available for full external flow. `AUDIT-E2E-001`. |
| M-13 | REAL | `/tasks/:id/data` → DB query/review commands | UI/API/repository/ownership command path is substantive; no fixed record data found. |
| M-14 | REAL | `/tasks/:id/quality` → runtime metrics/drill-down | Metrics are database-derived, not static UI values; complete external-run quality remains part of `AUDIT-E2E-001`. |
| M-15 | GAP | evidence/export/delete/cleanup → object storage | Direct CSV/artifact APIs are real and Production rows exist, but the planned `generate_artifact` execution node is unavailable. `AUDIT-ARTIFACT-001`. |
| M-16 | SYNTHETIC_ONLY | reliability/capacity acceptance | Reliability harness explicitly uses synthetic tasks and fixtures. `AUDIT-RELIABILITY-001`. |
| M-17 | REAL | immutable release, backup/restore, staging promotion | Historical deployment/restore records and current immutable images are verifiable. The incident release must repeat the gate. |
| M-18 | GAP | real golden task → complete Production outcome | Historical `DONE` conflicts with current evidence: smoke script uses retired `deepseek-chat`, and all live Runs are partial. `AUDIT-RELEASE-001`. |

## Findings

### AUDIT-PROVIDER-001

- Module: M-03, M-06
- Severity: P0
- Classification: HALF_WIRED + ENVIRONMENT_MISMATCH
- User-visible feature: `/models` model configuration and `/tasks/:id/chat` Goal Understanding.
- Expected behavior: selecting a Provider loads that vendor's real model IDs; connection testing and inference resolve the same registry, credential, base URL, model and transport facts.
- Actual behavior: Production allowed free-text `DeepSeek`; connection testing called only `GET /models` and returned `AVAILABLE` without checking the saved model; inference sent `DeepSeek`, received HTTP 400, and mapped it to `NETWORK_ERROR`.
- Code path: `ModelConfigDrawer.vue` → provider model config API → `ProviderService.test_model_connection` / `build_model_provider` → `ModelInferenceClient.complete` → DeepSeek.
- Evidence: Production Task 5 retained one user message and appended one assistant `NETWORK_ERROR`; selected config id `2722695f7f7b4d45a3204e6aab3cf45b`, version 1, provider `deepseek`, model `DeepSeek`, credential version 5. Controlled Vault-backed probe returned `AVAILABLE`, while the same resolved inference path returned `ProviderNetworkError` with HTTP 400. Live provider catalog returned `deepseek-v4-flash` and `deepseek-v4-pro`; `DeepSeek` was absent. DNS, TCP 443, TLS and API/worker egress succeeded.
- Production reachable: yes.
- Security impact: no secret leak was observed in the incident. Review of the new catalog boundary found that a saved credential could otherwise be paired with a different requested Provider; the incident fix now rejects Provider changes for existing credentials before any HTTP call and the edit UI locks the Provider field.
- Data impact: user input was preserved and not duplicated; GoalUnderstandingResult and CollectionSpecDraft were not produced.
- Recommended correction: D-075 real-time catalog endpoint, select-only UI, model-membership validation in the shared adapter, same-Provider credential binding, shared transport/config resolution, and correct HTTP 400 inference classification. Implemented on the incident branch; not yet Production until release gates complete.
- Related decisions/modules: D-023, D-024, D-029, D-051, D-066, D-073, D-075; M-03, M-06.
- Verification required: scoped regression suite, real DeepSeek Staging catalog/probe/inference, then Production catalog/probe/Goal Understanding with no secret in logs.

### AUDIT-PLAN-001

- Module: M-07
- Severity: P1
- Classification: HALF_WIRED
- User-visible feature: confirm a valid Plan and automatically start execution.
- Expected behavior: plan persistence and Temporal start have explicit, idempotent failure semantics; a user is not told the command succeeded when execution did not start.
- Actual behavior: `generate_plan` catches every exception across persistence and `auto_start`, logs “Temporal unavailable”, persists again through `_NoopStarter`, and returns a plan with no run/workflow identity. If the first persistence succeeded before `auto_start` failed, the fallback can attempt a second persist.
- Code path: `backend/app/api/routes/plans.py::generate_plan`, lines 119–145; `_NoopStarter` lines 50–61.
- Evidence: broad `except Exception` wraps both `persist_plan` and `auto_start`; fallback deliberately returns a no-op starter. Production has 1 Task still `QUEUED` and no completed Run.
- Production reachable: yes.
- Security impact: none identified.
- Data impact: plan/run state can diverge; duplicate-version attempts or a stranded plan are possible.
- Recommended correction: narrow Temporal startup errors, persist exactly once transactionally/idempotently, return a typed degraded/start-failed state, and provide an explicit retry command.
- Related decisions/modules: D-007, D-011, D-013, D-015, D-016, D-024, D-038; M-07.
- Verification required: Temporal unavailable regression, persist-once assertion, retry idempotency, Staging stop/start-worker smoke.

### AUDIT-ARTIFACT-001

- Module: M-08, M-15
- Severity: P1
- Classification: STUB_OR_NOOP + HALF_WIRED
- User-visible feature: generated Plan completes through artifact/CSV production.
- Expected behavior: every node admitted by the Plan validator and generator has a Production executor or is rejected before Run start.
- Actual behavior: `NodeType.GENERATE_ARTIFACT` is part of standard graphs and the model prompt, but the worker only installs discovery, fetch, extraction and validation executors. No `register_node_executor(NodeType.GENERATE_ARTIFACT, ...)` exists; execution resolves `NODE_EXECUTOR_UNAVAILABLE`.
- Code path: `backend/app/agents/plan_generator.py` → `backend/app/plan/nodes.py` → `backend/app/activities/plan_execution.py` → `backend/app/plan/executors.py` → `backend/app/worker.py`.
- Evidence: code-level registration inventory; Production has 3/3 Runs `partially_completed` and 0 completed Runs. Direct artifact APIs are real, so this is a workflow wiring defect, not fake CSV data.
- Production reachable: yes, when a generated graph contains the node.
- Security impact: none identified.
- Data impact: otherwise valid Runs cannot close successfully; final artifacts may require a separate manual action.
- Recommended correction: implement an idempotent executor backed by the existing artifact/export service, or remove the node from generation/validation until it exists. Do not return fixed success.
- Related decisions/modules: D-008, D-013, D-015, D-016, D-060, D-072; M-08, M-15.
- Verification required: node registry contract test, repeated-call idempotency, object-storage proof, real Staging Run to downloadable CSV.

### AUDIT-EXTRACTION-001

- Module: M-11
- Severity: P1
- Classification: HALF_WIRED
- User-visible feature: semantic extraction fallback and reproducible evidence.
- Expected behavior: semantic extraction uses the model config/version frozen into the Plan and fails explicitly when that immutable reference cannot be resolved.
- Actual behavior: the resolver can fall back to the user's current default model when Plan references are absent; broad resolver errors are collapsed to “no model”.
- Code path: extraction model resolver → `CredentialVault.read_for_execution` → `ModelInferenceClient` → evidence persistence.
- Evidence: static control-flow trace of the production resolver; no canned extraction result or fake provider was found.
- Production reachable: yes for semantic-fallback pages.
- Security impact: ownership remains enforced, but auditability is weakened.
- Data impact: retries of the same frozen Plan may use a later default model, changing extracted values/evidence.
- Recommended correction: require frozen `model_config_id + version` for semantic nodes, store a typed resolution failure, and prohibit current-default fallback after Plan freeze.
- Related decisions/modules: D-004, D-010, D-013, D-016, D-023, D-024, D-029; M-11.
- Verification required: config rotation regression proving an old Run continues to use its frozen version, typed missing-version failure, Temporal-history secret scan.

### AUDIT-E2E-001

- Module: M-09, M-10, M-11, M-12, M-14
- Severity: P1
- Classification: UNVERIFIED + SYNTHETIC_ONLY
- User-visible feature: search/discovery through fetch, extraction, validation, evidence and quality.
- Expected behavior: at least one current real external site task proves every layer and produces runtime-derived evidence.
- Actual behavior: adapters/executors/services are substantive, but current acceptance evidence is fixture/synthetic or partial. Dynamic browser external E2E was historically deferred. Production has snapshots/records/evidence, yet all Runs are partial.
- Code path: SearchProvider → SourceSearch → URL frontier/robots → HTTP/Playwright → snapshot → extraction → validation → records/evidence/quality.
- Evidence: worker registration and persistence counts, deployment records, synthetic harnesses, and absence of a completed live Run.
- Production reachable: yes, code path; full outcome unverified.
- Security impact: no direct issue found.
- Data impact: actual correctness/coverage on changing external sites is not currently proven.
- Recommended correction: a tiny allowlisted Staging golden task with real Tavily only after model success, one static and one browser-escalated page, expected evidence/partition assertions, then a bounded Production smoke.
- Related decisions/modules: D-003, D-009, D-010, D-013, D-014, D-068, D-069, D-070; M-09–M-12, M-14.
- Verification required: current real external E2E and retained redacted trace IDs.

### AUDIT-RELEASE-001

- Module: M-18
- Severity: P1
- Classification: ENVIRONMENT_MISMATCH + UNVERIFIED
- User-visible feature: release claim that the full real golden path is Production-ready.
- Expected behavior: release smoke discovers/uses a currently supported provider model and proves the deployed path.
- Actual behavior: `infra/scripts/_m18_production_smoke.py` hardcodes retired `deepseek-chat`; it cannot represent the provider's current model catalog. Historical `DONE` therefore does not prove the current release.
- Code path: M-18 production smoke → create model config → connection test → Goal Understanding → Plan/Run.
- Evidence: smoke line 78; current DeepSeek catalog contains `deepseek-v4-flash` and `deepseek-v4-pro`; Production has no completed Run.
- Production reachable: release operation, not normal product runtime.
- Security impact: the script correctly avoids printing the key; retain that property.
- Data impact: false-positive or obsolete release evidence can mask user-visible breakage.
- Recommended correction: query the same model-catalog API used by the UI, select a returned supported model, fail closed if catalog membership is absent, and separate Goal Understanding proof from full collection proof.
- Related decisions/modules: D-024, D-073, D-075; M-03, M-06, M-18.
- Verification required: execute revised smoke on Staging and Production against immutable digests.

### AUDIT-RELIABILITY-001

- Module: M-16
- Severity: P2
- Classification: SYNTHETIC_ONLY
- User-visible feature: reliability/capacity claim.
- Expected behavior: synthetic load proves deterministic capacity controls, while a separate real-path smoke proves external integration.
- Actual behavior: the M-16 harness intentionally uses synthetic tasks/fixtures. This is valid for deterministic load but insufficient as external-path evidence.
- Code path: M-16 reliability scripts/harnesses.
- Evidence: explicit fixture/synthetic setup in execution docs and worker gates.
- Production reachable: no; test facility only.
- Security impact: none.
- Data impact: none directly; risk is overclaiming coverage.
- Recommended correction: retain the deterministic harness, label its scope, and pair it with Closure-05 real-path evidence.
- Related decisions/modules: D-015, D-016, D-024, D-071; M-16.
- Verification required: synthetic gate plus distinct real external smoke, reported separately.

### AUDIT-OBSERVABILITY-001

- Module: M-17, M-18
- Severity: P2
- Classification: UNVERIFIED
- User-visible feature: operators can diagnose provider/external failures without exposing secrets.
- Expected behavior: Goal Understanding, search, fetch, extraction and workflow failures have correlated redacted traces and stable error classes.
- Actual behavior: tracing/redaction code and OTEL containers are real; this audit verified the provider incident from DB/runtime probes, but no current end-to-end trace was retained for a successful full external Run.
- Code path: API/worker logging → OTEL collector → release/smoke diagnostics.
- Evidence: Production OTEL collector and redaction tests; missing completed Run trace.
- Production reachable: yes.
- Security impact: no leak found; unstructured diagnostics increase future handling risk.
- Data impact: operational, not direct business-data mutation.
- Recommended correction: retain safe metadata (`provider_type`, config/version, model, non-secret base URL, status class, latency, trace id) for Closure-05 smoke and add a log-secret scanner gate.
- Related decisions/modules: D-023, D-024, D-029; M-17, M-18.
- Verification required: successful trace correlation and negative secret scan in Staging/Production smoke windows.

## Finding totals

| Severity | Count |
| --- | ---: |
| P0 | 1 |
| P1 | 5 |
| P2 | 2 |
| P3 | 0 |
| **Total** | **8** |

## Test-only mocks and accepted fixtures

The following are not Production defects:

- `MagicMock`, `AsyncMock`, fake repositories/transports and fixture provider responses under `backend/tests/**` and frontend test files. They are dependency-isolated test facilities.
- Pydantic AI `FunctionModel` in Goal Understanding. Its callback delegates every Production invocation to `ModelInferenceClient`; it does not manufacture a canned `GoalUnderstandingResult` and has no silent fake fallback.
- `plan_fixture_mode` staging executor. It is installed only when the explicit setting is true; Production is running with `KAIROS_ENV=production` and no fixture-mode setting.
- `_NoopStarter` used by read-only Plan summary construction. That usage does not start a workflow. The defect is its reuse in the write-command exception fallback described by `AUDIT-PLAN-001`.
- Fixture HTML/search/model results in scoped unit/integration tests. The gap is the absence of current external E2E proof, not the existence of fixtures.

## Frontend-specific result

No Production source path was found that substitutes static Task, Record, quality, evidence, Provider status, execution timeline or SSE progress data for backend facts. No business-success `setTimeout` simulation or `Math.random` result path was found. UI pages and drawers use API clients; explicit empty states are acceptable under M-05. The incident branch removes the one dangerous user-entered fact: arbitrary model IDs.

## Historical status versus verified status

Historical records declare M-18 `DONE`/Production Ready. The current verified status is `GAP`: the Goal Understanding incident is reproducible on the running release; the historical smoke model is retired; and all three Production Runs are partial. This audit does not rewrite historical records. It records the difference and requires new immutable-release evidence before making a current readiness claim.

## Immediate release boundary

This incident release includes only `AUDIT-PROVIDER-001`, the D-075 catalog UX/API, the directly required error/parity regression tests, and the current release-smoke catalog correction. The remaining P1/P2 findings are ordered in the companion closure plan. None will be disguised as fixed by the DeepSeek Goal Understanding release.
