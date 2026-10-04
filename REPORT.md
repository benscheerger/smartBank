# Design Report

## Architecture

smartBank retrieves a member’s available savings balance and currency from a local banking demo using synthetic records.

Discovery follows an observe → decide → act loop. Playwright reads the page, the model proposes a typed action, and application code checks policy before executing it. Discovery starts with an eight-decision window. When explicitly enabled, one validated human handoff can grant one additional eight-decision window. Separate UI verification must succeed before the recorded workflow becomes a reusable capability.

The discovery request includes an explicit target URL. The implementation accepts only the allowlisted local smartBank demo URL and uses that value for policy validation and initial navigation; it does not accept arbitrary web targets.

Replay follows the saved workflow without model calls. The Flask operator console uses shared discovery and replay functions. HTTP handlers queue commands; the thread controlling the browser performs Playwright operations.

| Decision | Reason | Tradeoff |
| --- | --- | --- |
| Python and Flask | Familiar tools keep the demo and console straightforward. | Active jobs cannot be recovered after a process restart. |
| Playwright and accessibility snapshots | Labels and roles help the model understand pages and identify controls. | Useful labels and compatible page structure are required. |
| Structured model responses | Pydantic validates proposed actions before execution. | Discovery is limited to supported action types. |
| Separate planning and execution | Application code controls permissions and browser operations. | Supporting another action requires an explicit implementation. |
| Discovery followed by replay | Recorded workflows avoid repeated model decisions. | Replay still needs checks for unexpected application behavior. |
| Synthetic demo | Known records and injected notices make testing reproducible. | Results provide limited evidence of use on unfamiliar applications. |

## Artifact schema

The capability stores the reusable workflow rather than the model conversation.

| Field | Purpose |
| --- | --- |
| `schema_version` | Format version. New recordings use `1.3`; the loader upgrades valid `1.1` and `1.2` artifacts in memory. |
| `name` | Supported task: `get_savings_balance`. |
| `source_run_id` | Discovery run that created it. |
| `start_path` | Entry path, currently `/`. |
| `input_type` | Names `MemberLookupInputs`, which validates the member ID. |
| `input_schema` | Embeds the complete JSON Schema for replay inputs. |
| `output_type` | Names `ReplayResult`, covering success, a business outcome, or failure. |
| `output_schema` | Embeds the serialization JSON Schema for all replay result variants. |
| `verifier` | Names the task-specific result check, `savings_balance_v1`. |
| `steps` | Ordered actions and expected page paths. |

The embedded schemas are generated from the Pydantic models. They include the required member-ID pattern, the three discriminated result variants, required recovery events, and the serialized string form of a balance. Version `1.3` loading checks both schemas against the current contracts. Version `1.2` loading checks a literal frozen input schema and frozen legacy output models, independently of current input-model changes. Existing run-specific `1.1` and `1.2` artifacts remain unchanged on disk and are upgraded only in memory.

Fields are targeted by label, buttons by name, and replayed links by exact destination. Playwright rejects ambiguous targets instead of selecting an arbitrary match.

Templates combine fixed text with input references. A member link contains `/members/` plus `member_id`, allowing reuse without storing the original member’s name. These targets are easier to review than coordinates but depend on compatible labels and routes.

Strict schemas reject unexpected fields, invalid constrained values, altered embedded contracts, and explicitly unsupported versions.

## Determinism & error handling

Replay resolves validated inputs into recorded actions. It does not ask the model to select actions or repair failures. The action order stays fixed, although displayed application data can change.

Playwright waits for actionable controls within configured timeouts. Path checkpoints check progress. Final verification confirms the requested member and account, reads a finite numeric balance, and checks for three uppercase currency letters. This verifies displayed UI evidence separately from the model’s claim; it does not confirm the balance against an independent banking record.

| Condition | Response |
| --- | --- |
| Missing member | Return `business_outcome` with `member_not_found`. |
| Known blocking notice | Attempt automatic dismissal once per run. |
| Persistent notice at the supported checkpoint | Request human takeover when enabled. |
| Missing target, checkpoint mismatch, policy violation, or failed verification | Stop and return a structured failure. |

All three replay result variants contain `recovery_events`. A clean run returns an empty list. Notice handling records automatic recovery, recovery by a human, exhaustion, cancellation, or timeout. Failures use specific `recovery_exhausted`, `human_takeover_cancelled`, and `human_takeover_timed_out` codes rather than collapsing those conditions into verification failures.

Handled UI failures remain in the capability's `ReplayResult`. The outer replay job uses a separate discriminated envelope: `replay_result` carries that capability result, while `job_failure` reports capability loading, evidence, environment, or unexpected orchestration errors with a safe stage and exception type. This keeps capability schema `1.3` stable. Failures after logging starts retain a run ID and terminal `run_failed` event; pre-log failures have no run ID.

Saved events record the target URL, execution, verification, recovery, and handoff. New logs use evidence schema `1.2`; archived `1.0` and `1.1` logs remain readable without modification. Discovery action proposals record provider, returned model name, response ID, and nullable token counts without model content. Action events include fixed purpose labels, such as `open_member_details`, to explain their intent without storing member details or model-generated reasoning. Replay failures and discovery failures with an available page attempt to capture a bounded structural snapshot and record when capture or writing is unavailable. New failure captures use schema `1.1`; archived `1.0` captures remain readable.

## Heterogeneity & multi-tenant

The implementation supports one web application. Alternate datasets demonstrate reuse across records, not different vendor interfaces.

The current artifact and executor are web-specific: they encode HTML labels, link destinations, URL paths, and a Playwright page. The adapter design below is an extension plan, not an implemented cross-surface abstraction.

I would introduce surface adapters responsible for observation, actions, and reading verification values. A web adapter could handle labels, links, frames, and application-specific selectors. A desktop adapter could use operating-system accessibility controls, with image matching or OCR where needed.

The recorded workflow would reference logical targets such as “member search field.” Each adapter would map those targets to actual controls. Unreliable or ambiguous mappings would stop execution.

Institutions using compatible versions of the same vendor application could share a workflow. Separate tenant settings would contain entry URLs, policies, and control mappings. Artifact metadata would identify supported application and adapter versions.

Compatibility checks would run before reuse. Tenant overrides would be explicit and tested. Changed screen sequences could require a separate workflow version rather than silently changing an existing capability.

## Escalation & handoff

Human takeover handles a persistent service notice at the member-details checkpoint after automatic recovery is exhausted. Discovery can request one handoff after the initial eight-step budget or an actionable target timeout. Policy violations, model or parsing failures, and lost browser sessions do not trigger takeover.

Replay and discovery pause on the original browser page and context. The console and CLI panel provide the task or capability, mode, reason, member, step, owner, timeout, and Resume and Cancel controls. Takeover states distinguish human control, Resume validation, and return to automation.

Replay Resume checks the original single-tab session, policy, the requested member-details page, removal of the notice, and one actionable pending link. A repairable replay checkpoint failure leaves automation paused. Discovery Resume requires the same URL, unchanged member input, one open page, no blocked traffic, and only known notice-repair controls; a workflow-changing action or other invalid session stops discovery. A validated discovery Resume grants one additional eight-step window with continuous numbering. Human actions are never added to the capability.

One takeover is allowed per run, with a default 180-second timeout. Cancellation or expiry produces a structured failure. Evidence records control transfers, rejected Resume requests, and up to 100 categorized manual actions without entered values.

End-to-end tests exercise clean replay, automatic recovery, the missing-member outcome, exhausted recovery, successful takeover, cancellation, timeout, checkpoint mismatch, discovery continuation, rejected discovery Resume, failure capture, model metadata, and WebSocket blocking.

## Safety

A configurable policy restricts origins, paths, HTTP methods, action types, fields, and buttons. Each job adds a request scope that permits only the exact member search, requested member details, and requested member’s savings account; custom policy files cannot broaden that scope. Entered member IDs and link destinations are checked before execution. A browser request guard blocks disallowed intercepted requests and HTTP redirects, closes all WebSockets before they connect, and records each block through the existing policy-violation channel; job functions disable service workers.

The implemented banking task only reads account information. Configured risky button names take precedence over permitted button names. The policy test checks this behavior with a button named Transfer.

Model-provided code is not executed. Human takeover retains the request guard, and Resume cannot override a policy violation.

Saved JSONL logs omit prompts, raw observations, model response content, summaries, and entered values. Failure capture retains limited DOM structure and control counts without page text, field values, or raw attributes. Privacy tests check that named sensitive sentinel values are absent from replay and discovery failure fixtures. Terminal diagnostics contain only safe run metadata, redacted identifiers, and fixed action, purpose, status, recovery, and failure fields. Caller-facing results retain the verified banking output and model summary where intended; terminal diagnostics do not emit raw model, page, input, or verified banking content.

Discovery supports loading the API key from outside the repository. Console requests use token, Host, and POST Origin checks, but there is no user-login system.

These controls are not an operating-system sandbox. Discovery sends synthetic page observations to the model; sanitizing saved evidence does not protect information sent in model requests.

## Cuts

I focused on one complete workflow so discovery, recording, replay, verification, recovery, and takeover could be tested together.

I left out additional tasks, desktop execution, tenant-specific workflows, escalation beyond the one supported discovery handoff, session-expiry recovery, remote co-browsing, user accounts, and job recovery after a restart. Changed routes or controls are not repaired automatically.

Next, I would add more tasks to the existing banking UI and support more safe escalation cases. After that, I would introduce a surface adapter and test a second UI variant.
