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

OpenAI was chosen for its structured responses, which the Python SDK parses using the Pydantic `NextAction` model. The tested `gpt-6-luna` completed the implemented lookup; replay makes no model calls. The prompt supplies the goal and page observation and instructs the model to treat page content as data. Application code checks permissions, executes actions, records steps, and verifies results. Using another provider would require changes to the planner and provider metadata; execution and verification could remain separate.

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

New capabilities include input and output JSON Schemas generated from Pydantic models. Pydantic validates the artifact fields, and the loader compares embedded schemas with the supported definitions. Unsupported versions are rejected. Older supported artifacts are converted in memory without changing their saved files.

Fields are targeted by label, buttons by name, and links by exact destination. Playwright rejects ambiguous targets. Templates combine fixed text with input references, so a member link can reuse `member_id` without storing the original member’s name. These targets are easier to review than coordinates but still depend on compatible labels and routes.

## Determinism & error handling

Replay resolves validated inputs into recorded actions. It does not ask the model to choose actions or repair failures. The action order stays fixed, although displayed data can change.

Playwright waits for actionable controls. Replay checks the expected path after each step. Final verification confirms the requested member and account, reads a finite numeric balance, and requires a three-letter uppercase currency. It checks the displayed UI separately from the model’s summary, without comparing against an independent bank record.

| Condition | Response |
| --- | --- |
| Missing member | Return `business_outcome` with `member_not_found`. |
| Known blocking notice | Attempt automatic dismissal once. |
| Persistent supported notice | Request human takeover when enabled. |
| Missing target, wrong checkpoint, policy violation, or failed verification | Stop with a structured failure. |

Every `ReplayResult` includes a `recovery_events` list, which is empty when no recovery occurs. Notice handling records automatic recovery, human recovery, exhaustion, cancellation, or timeout. Failures use separate codes for recovery exhaustion, takeover cancellation, and takeover timeout.

Handled replay failures return through `ReplayResult`. The job function returns either that result or a `job_failure` for problems loading the capability, writing evidence, starting or closing the environment, or running the job. Failures after logging starts retain their run ID. The logger attempts to record `run_failed`, but a logging error can prevent that event from being saved.

New evidence logs record the target, actions, verification, recovery, handoff, and model-call metadata. They omit prompts, raw observations, response content, entered values, and model reasoning. Handled replay failures and discovery failures with an available page attempt a limited structural capture. An unavailable snapshot is marked in the capture; if the file cannot be written, the logger attempts to record that failure evidence is unavailable.

## Heterogeneity & multi-tenant

The implementation supports one web application. Alternate datasets demonstrate reuse across records. Support for other applications and institutions remains future work.

I would separate the recorded workflow from application interaction through adapters. The workflow would use logical targets such as “member search field.” An adapter would read the application, find controls, perform actions, check progress, and extract values. Web adapters could use labels, roles, and frames. Desktop adapters could use operating-system accessibility controls or image-based targeting.

I would track the artifact format, workflow, adapter, and supported application versions separately. The artifact and institution settings would select a compatible adapter. Execution would stop if none matched. Older artifacts could receive adapter information during loading without changing their saved files.

Institutions using compatible versions of the same vendor application could share a workflow. Vendor control mappings would apply first, followed by institution-specific settings. These settings could change entry URLs, control mappings, or narrow permissions. They could not change inputs, outputs, step order, result checks, or broaden permissions.

Before execution, the system would check supported versions, the entry URL, and expected starting controls. At each step, a target would need exactly one matching control. Missing or ambiguous targets would stop execution. Changed controls could use updated mappings; changed screen sequences would require a new workflow version. Changes to the JSON format could be handled during loading.

## Escalation & handoff

Replay can request human takeover when one automatic dismissal attempt cannot clear the known service notice. Discovery can request takeover after an action-target timeout or its initial eight-decision limit. Policy violations, model failures, and lost browser sessions stop execution instead.

Automation pauses in the same browser session. The operator sees the live page, while the panel shows the task, mode, stop reason, step, owner, timeout, and Resume and Cancel controls.

Replay Resume checks the original single-tab session, policy, requested member-details page, absence of the notice, and one actionable pending link. A repairable checkpoint rejection leaves replay paused.

Discovery Resume checks the interrupted URL, unchanged member input, original single-tab session, and policy. Any previously visible notice must be cleared. Recorded manual actions outside the known notice controls are rejected. Failed Resume validation stops discovery; successful validation allows up to eight additional model decisions.

One takeover is allowed per run, with a default timeout of 180 seconds. Cancellation, expiry, or loss of the required session stops the run. Evidence records control transfers, rejected Resume requests, and up to 100 categorized manual actions without entered values. Human actions are not added to the saved capability.

## Safety

A configurable policy restricts origins, paths, request methods, actions, fields, and buttons. Each job also restricts navigation and entered values to the requested member and that member’s savings account. Custom policy files cannot bypass those member and account restrictions. IDs and destinations are checked before execution.

A browser guard blocks disallowed requests, redirects, and WebSockets, and job functions disable service workers. Risky button names override permitted names. The demonstrated task only reads account information. Model-provided code is never executed. Human takeover keeps the request guard, and Resume cannot override a policy violation.

Logs omit prompts, raw observations, response content, summaries, entered values, and verified banking data. Failure captures keep limited structure and control counts without page text, field values, or raw attributes. Terminal output uses redacted identifiers and fixed status fields. Caller-facing results and the authenticated local console still return the verified task output.

The API key loads from outside the repository. Console requests use token, Host, and POST Origin checks, but there is no user-login system. These controls are not an operating-system sandbox. Discovery still sends synthetic page observations to the model.

## Cuts

I focused on one complete workflow so discovery, recording, replay, verification, recovery, and takeover could be tested together.

I left out other tasks, desktop execution, tenant-specific workflows, broader escalation, session-expiry recovery, remote co-browsing, user accounts, and restart recovery. Next I would add tasks and safe recovery cases, then implement the adapter seam against a second UI variant.
