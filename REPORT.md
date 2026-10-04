# Design Report

## Architecture

smartBank retrieves a member’s available savings balance and currency from a local banking demo using synthetic records.

Discovery follows an observe → decide → act loop. Playwright reads the page, the model proposes one typed action, and code checks policy before execution. Discovery starts with an eight-decision window. One validated human handoff can grant one additional window. Separate UI verification must succeed before the workflow becomes reusable.

The request includes a target URL, but only the allowlisted local demo is accepted. Replay follows the saved workflow without model calls. The Flask console queues commands for the browser-control thread.

| Decision | Reason | Tradeoff |
| --- | --- | --- |
| Python and Flask | Familiar tools keep the demo and console straightforward. | Active jobs are not recovered after a restart. |
| Playwright and accessibility snapshots | Labels and roles provide useful control descriptions. | Compatible labels and page structure are required. |
| Structured model responses | Pydantic validates each proposed action. | Discovery is limited to supported action types. |
| Separate planning and execution | Application code controls permissions and browser operations. | New actions need explicit support. |
| Discovery followed by replay | Saved workflows avoid repeated model decisions. | Replay still needs runtime checks. |
| Synthetic demo | Known records and notices make tests reproducible. | It gives limited evidence for unfamiliar applications. |

OpenAI was chosen because its Responses API returns the strict Pydantic `NextAction` type. The tested `gpt-6-luna` completed bounded discovery; replay does not use it. The prompt sends the goal and structural observation, treats page content as data, and permits only supported actions. Policy, execution, recording, and verification stay in code. Another provider can replace `propose_action` by returning the same `NextAction` and `ModelCallMetadata` contracts.

## Artifact schema

The capability stores the reusable workflow rather than the model conversation.

| Field | Purpose |
| --- | --- |
| `schema_version` | Version of the serialized format. New recordings use `1.3`. |
| `name`, `source_run_id` | Supported task and discovery provenance. |
| `start_path`, `steps` | Entry path, ordered actions, and expected paths. |
| `input_type`, `input_schema` | Typed replay inputs and their complete JSON Schema. |
| `output_type`, `output_schema` | Success, business outcome, and failure contracts. |
| `verifier` | Task-specific final result check. |

The embedded schemas come from the Pydantic models. Strict loading rejects extra fields, invalid values, changed contracts, and unsupported versions. Older valid artifacts are upgraded in memory and are not rewritten; the README gives the version details.

Fields are targeted by label, buttons by name, and links by exact destination. Playwright rejects ambiguous targets. Templates combine fixed text with input references, so a member link can reuse `member_id` without storing the original member’s name. These targets are easier to review than coordinates but still depend on compatible labels and routes.

## Determinism & error handling

Replay resolves validated inputs into recorded actions. It does not ask the model to choose actions or repair failures. The action order stays fixed, although displayed data can change.

Playwright waits for actionable controls and checks the expected path after each step. Final verification confirms the member and account, reads a finite balance, and requires a three-letter uppercase currency. This checks the UI separately from the model’s claim; it does not compare against an independent bank record.

| Condition | Response |
| --- | --- |
| Missing member | Return `business_outcome` with `member_not_found`. |
| Known blocking notice | Attempt automatic dismissal once. |
| Persistent supported notice | Request human takeover when enabled. |
| Missing target, wrong checkpoint, policy violation, or failed verification | Stop with a structured failure. |

Every replay result includes `recovery_events`. Notice handling records automatic recovery, human recovery, exhaustion, cancellation, or timeout. Specific codes keep those conditions separate from verification failures.

Handled UI failures stay inside the capability’s `ReplayResult`. A separate replay-job envelope returns either that result or a `job_failure` for loading, evidence, environment, or unexpected orchestration errors. Failures after logging starts keep the run ID and end with `run_failed`; earlier failures have no run ID. This leaves capability schema `1.3` unchanged.

Evidence records the target, actions, verification, recovery, handoff, and safe model-call metadata. It omits prompts, observations, response content, entered values, and reasoning. Failures attempt a bounded structural capture and record when it is unavailable.

## Heterogeneity & multi-tenant

The implementation supports one web application. Datasets show record reuse, not vendor reuse. The artifact and executor are web-specific.

The proposed seam keeps logical steps in the artifact and moves surface work into an adapter. Each adapter would observe, resolve targets, act, check checkpoints, and read values. Modern web, legacy web, and desktop adapters could use labels, frames, accessibility controls, image matching, or OCR without changing the logical flow.

Four versions would stay separate: artifact schema, workflow, adapter, and supported vendor application. Older artifacts without adapter metadata would migrate in memory to the smartBank web profile and stay unchanged on disk. A registry would select an adapter from the artifact and tenant settings. The surface kind, application family, and adapter major version must match; the newest compatible minor version would be used. No match would stop execution.

Configuration would resolve in this order: shared vendor artifact, vendor-version control mapping, then tenant override. An override could change the entry URL, logical control mappings, or make policy narrower. It could not change inputs, outputs, step order, verification, or broaden permissions.

A preflight check would compare the vendor version, entry route, adapter version, and a small application signature made from required controls. Every logical target must resolve once. Replay would continue checking recorded checkpoints and stop on the first missing or ambiguous mapping. A control-only difference needs a mapping override. A changed screen sequence needs a new workflow version. A serialization-only change uses an artifact migration.

## Escalation & handoff

Human takeover handles a persistent service notice after automatic recovery is exhausted. Discovery can also request one handoff after its decision budget or an actionable-target timeout. Policy violations, model failures, and lost browser sessions stop instead.

Automation pauses on the original browser page and context. The console shows the task, mode, reason, step, owner, timeout, and Resume and Cancel controls. Resume checks the same single-tab session, policy, member page, notice state, and pending control. Discovery also checks that the member input and workflow have not changed. A failed but repairable check leaves automation paused; an unsafe session stops the run.

One takeover is allowed per run, with a 180-second timeout. Evidence records control transfers, rejected Resume requests, and categorized manual actions without values. Human actions are not saved in the capability.

## Safety

A configurable policy restricts origins, paths, methods, actions, fields, and buttons. Each job narrows it to the requested member and savings account; custom policy files cannot widen it. IDs and destinations are checked before execution.

A browser guard blocks disallowed requests, redirects, and WebSockets, and job functions disable service workers. Risky button names override permitted names. The demonstrated task only reads account information. Model-provided code is never executed. Human takeover keeps the request guard, and Resume cannot override a policy violation.

Logs omit prompts, raw observations, response content, summaries, entered values, and verified banking data. Failure captures keep limited structure and control counts without page text, field values, or raw attributes. Terminal output uses redacted identifiers and fixed status fields. Caller-facing results and the authenticated local console still return the verified task output.

The API key loads from outside the repository. Console requests use token, Host, and POST Origin checks, but there is no user-login system. These controls are not an operating-system sandbox. Discovery still sends synthetic page observations to the model.

## Cuts

I focused on one complete workflow so discovery, recording, replay, verification, recovery, and takeover could be tested together.

I left out other tasks, desktop execution, tenant-specific workflows, broader escalation, session-expiry recovery, remote co-browsing, user accounts, and restart recovery. Next I would add tasks and safe recovery cases, then implement the adapter seam against a second UI variant.
