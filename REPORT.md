# Design Report

## Architecture

smartBank retrieves a member’s available savings balance and currency from a local banking demo using synthetic records.

Discovery follows an observe → decide → act loop. Playwright reads the page, the model proposes a typed action, and application code checks policy before executing it. Discovery is limited to eight model decisions. Separate UI verification must succeed before the recorded workflow becomes a reusable capability.

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
| `schema_version` | Format version, currently `1.1`. |
| `name` | Supported task: `get_savings_balance`. |
| `source_run_id` | Discovery run that created it. |
| `start_path` | Entry path, currently `/`. |
| `input_type` | Names `MemberLookupInputs`, which validates the member ID. |
| `output_type` | Names `ReplayResult`, covering success, a business outcome, or failure. |
| `verifier` | Names the task-specific result check, `savings_balance_v1`. |
| `steps` | Ordered actions and expected page paths. |

The type and verifier names refer to definitions in application code; the artifact does not embed their full definitions.

Fields are targeted by label, buttons by name, and replayed links by exact destination. Playwright rejects ambiguous targets instead of selecting an arbitrary match.

Templates combine fixed text with input references. A member link contains `/members/` plus `member_id`, allowing reuse without storing the original member’s name. These targets are easier to review than coordinates but depend on compatible labels and routes.

Strict schemas reject unexpected fields, invalid constrained values, and explicitly unsupported versions. The saved-artifact loader also requires version and output-type metadata.

## Determinism & error handling

Replay resolves validated inputs into recorded actions. It does not ask the model to select actions or repair failures. The action order stays fixed, although displayed application data can change.

Playwright waits for actionable controls within configured timeouts. Path checkpoints check progress. Final verification confirms the requested member and account, reads a finite numeric balance, and checks for three uppercase currency letters. This verifies displayed UI evidence separately from the model’s claim; it does not confirm the balance against an independent banking record.

| Condition | Response |
| --- | --- |
| Missing member | Return `business_outcome` with `member_not_found`. |
| Known blocking notice | Attempt automatic dismissal once per run. |
| Persistent notice at the supported checkpoint | Request human takeover when enabled. |
| Missing target, checkpoint mismatch, policy violation, or failed verification | Stop and return a structured failure. |

Handled replay failures include the step when available, the expected condition, safe observations, and the error type. Other exceptions propagate to the caller; the console catches them and displays a failed run.

Saved events record execution, verification, recovery, and handoff. Action events include fixed purpose labels, such as `open_member_details`, to explain their intent without storing member details or model-generated reasoning. Replay attempts to capture a bounded structural snapshot on failure and records when capture or writing is unavailable.

## Heterogeneity & multi-tenant

The implementation supports one web application. Alternate datasets demonstrate reuse across records, not different vendor interfaces.

I would introduce surface adapters responsible for observation, actions, and reading verification values. A web adapter could handle labels, links, frames, and application-specific selectors. A desktop adapter could use operating-system accessibility controls, with image matching or OCR where needed.

The recorded workflow would reference logical targets such as “member search field.” Each adapter would map those targets to actual controls. Unreliable or ambiguous mappings would stop execution.

Institutions using compatible versions of the same vendor application could share a workflow. Separate tenant settings would contain entry URLs, policies, and control mappings. Artifact metadata would identify supported application and adapter versions.

Compatibility checks would run before reuse. Tenant overrides would be explicit and tested. Changed screen sequences could require a separate workflow version rather than silently changing an existing capability.

## Escalation & handoff

Human takeover handles a persistent service notice at the member-details checkpoint after automatic recovery is exhausted. General escalation during discovery is not implemented.

Replay pauses on the original browser page. The console provides run, member, and step context with Resume and Cancel controls. CLI takeover uses a temporary operator panel. Takeover states distinguish human control, Resume validation, and return to automation.

Resume checks the original single-tab session, policy, the requested member-details page, removal of the notice, and one actionable pending link. Repairable checkpoint failures leave automation paused. Policy violations or loss of the required session end the run.

One takeover is allowed per run, with a default 180-second timeout. Cancellation or expiry produces a structured failure. Evidence records control transfers, rejected Resume requests, and up to 100 categorized manual actions without entered values.

Workflow tests exercise rejected Resume, manual notice repair, and successful continuation using scripted operator controls.

## Safety

A configurable policy restricts origins, paths, HTTP methods, action types, fields, and buttons. Link destinations are checked before execution. A browser request guard blocks disallowed intercepted requests and HTTP redirects; job functions disable service workers.

The implemented banking task only reads account information. Configured risky button names take precedence over permitted button names. The policy test checks this behavior with a button named Transfer.

Model-provided code is not executed. Human takeover retains the request guard, and Resume cannot override a policy violation.

Saved JSONL logs omit raw observations and model conversations. Failure capture retains limited DOM structure and control counts without page text, field values, or raw attributes. A privacy test checks that named sensitive sentinel values are absent from its saved failure fixture. Terminal output includes proposed actions and the model summary.

Discovery supports loading the API key from outside the repository. Console requests use token, Host, and POST Origin checks, but there is no user-login system.

These controls are not an operating-system sandbox. Discovery sends synthetic page observations to the model; sanitizing saved evidence does not protect information sent in model requests.

## Cuts

I focused on one complete workflow so discovery, recording, replay, verification, recovery, and takeover could be tested together.

I left out additional tasks, desktop execution, tenant-specific workflows, general discovery escalation, session-expiry recovery, remote co-browsing, user accounts, and job recovery after a restart. Changed routes or controls are not repaired automatically.

Next, I would expand runtime-failure tests and support more safe escalation cases. Then I would add more tasks to the existing banking UI. After that, I would introduce a surface adapter and test a second UI variant.