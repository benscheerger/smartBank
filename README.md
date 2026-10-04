# smartBank

## LLM banking UI discovery and deterministic replay

Some business applications have no integration API, so automation must use their UI. smartBank demonstrates how an LLM can discover a workflow and save it for reuse without further model calls.

The implemented task retrieves a member’s available savings balance and currency from a local banking demo using synthetic records. Discovery produces a validated JSON capability containing reusable actions, input and output schemas, input references, and checkpoints. Replay follows those actions with another member ID and checks the displayed result.

An operator console provides discovery, replay, dataset selection, results, and run history. When enabled, a human can repair a supported service notice in the same browser session and request Resume.

## Features and scope

- **Discovery:** Accepts a natural-language goal, an allowlisted target URL, and a member ID. The model reads the page and proposes actions within an eight-decision window, with one bounded handoff and one additional window when enabled.
- **Reusable capabilities:** Saves verified workflows as versioned JSON files with self-contained input and output contracts.
- **Replay:** Runs saved workflows with new inputs without model calls.
- **Clear outcomes:** Separates success, missing-member results, and handled execution failures while reporting recovery events to the caller.
- **Recovery and takeover:** Attempts one automatic notice dismissal, then allows bounded human repair during replay or discovery when enabled.
- **Operator console:** Provides one interface for running tasks and inspecting evidence.
- **Policy and evidence:** Checks permitted actions and requests, blocks WebSockets, records action purposes and model-call metadata, and attempts structural failure capture.

Natural-language goals are limited to the implemented savings-balance task. Alternate datasets demonstrate reuse across records in the same UI. Desktop applications, other banking tasks, and real banking integrations are not implemented.

## Requirements and installation

### Requirements

- Python 3.11.
- A desktop session for the visible Chromium browser.
- An OpenAI API key for live discovery.

Replay and the default test suite make no model calls and do not require an API key. Their workflow checks require an existing saved capability.

### Installation

Clone the repository and run these commands from its root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The activation command is for macOS and Linux. Run later commands from the repository root with this environment active.

If `python` selects another installation, use `./.venv/bin/python` instead. Moving or renaming the repository may require recreating the virtual environment.

The included `pyrightconfig.json` points Pyright and VS Code Pylance at this `.venv` and Python 3.11.

### API configuration

Create the credential file outside the repository:

```bash
mkdir -p ~/.config/computer-use-automation
nano ~/.config/computer-use-automation/.env
```

Add:

```dotenv
OPENAI_API_KEY=your_api_key_here
```

The loader still uses the `computer-use-automation` directory name. Keeping this file outside the repository reduces accidental commits; it does not prevent filesystem access.

The model is selected by `MODEL` in `automation/planner.py`. The tested model is `gpt-6-luna`. Discovery uses the model API; replay does not.

### Policy configuration

The default policy is in `config/policy.json`. It defines permitted origins, paths, request methods, action types, fields, and buttons. Each run further restricts navigation and entered values to the requested member and that member’s savings account. Configured risky button names remain blocked even if included in the permitted-button list.

`AUTOMATION_POLICY_FILE` can select a different policy file when starting the program. Policy changes take effect when the process starts.

## Usage and end-to-end demonstration

Managed jobs start and stop the demo server automatically. Port `8000` must be available.

### Launch the operator console

```bash
python -m scripts.run_console
```

The launcher prints the console URL and attempts to open it in your browser. If needed, open the printed URL manually. The console supports one active run at a time.

### Discover a capability

1. Select **Discovery**.
2. Choose the `members` dataset.
3. Enter member ID `DEMO-101`.
4. Confirm the local smartBank demo target.
5. Choose whether to allow human takeover.
6. Enter this goal: “Find this member’s available savings balance and report its currency.”
7. Start the run.

A visible Chromium browser opens. After UI verification succeeds, the console displays the model summary, verified output, run ID, and capability ID.

Each successful discovery saves:

```text
evidence/capabilities/get_savings_balance_<run-id>.json
```

It also updates `get_savings_balance.json`, the convenience alias for the latest successful recording.

New recordings use capability schema `1.3`. Their embedded input and output JSON Schemas are generated from the runtime Pydantic models and validated when loaded. Existing `1.1` and `1.2` artifacts remain supported through an in-memory compatibility upgrade; the version `1.2` input schema is fixed independently of the current input model, its output contract uses frozen legacy models, and loading archived artifacts does not modify their files.

### Replay with another member

1. Select **Replay**.
2. Choose the capability created by discovery.
3. Choose the `members` dataset.
4. Enter member ID `DEMO-202`.
5. Set the notice mode to **Off** and disable human takeover.
6. Start replay.

Replay checks page-path checkpoints and reads the result from the account page. Verification confirms the requested member and account, a finite numeric balance, and a three-letter uppercase currency value. It does not compare against an independent banking record.

To demonstrate dataset reuse, select `members_alternate` and replay the same capability. The displayed result should reflect that dataset.

### Command-line demonstration

Stop the console and any separately running demo server, then run:

```bash
python -m scripts.demo
```

This discovers the workflow for `DEMO-101` and replays the resulting capability for `DEMO-202`. Discovery requires the API key.

Both runs write evidence under `evidence/runs/`. Replay records the original discovery ID as `source_run_id`.

### Run without model services

Start the console, select **Replay**, and choose an existing saved capability. This operates the local demo without an API key or model calls.

## Recovery and human takeover

For an existing member and a compatible saved capability:

| Notice mode | Takeover | Expected behavior |
| --- | --- | --- |
| Off | Disabled | Normal replay and result verification. |
| Dismissible | Disabled | One automatic dismissal attempt, followed by replay. |
| Persistent | Disabled | A structured failure after dismissal cannot clear the notice. |
| Persistent | Enabled | Human takeover at the member-details checkpoint. |

Recovery handles the known service notice. It does not attempt general page repair.

### Demonstrate takeover

1. Select **Replay**, a saved capability, the `members` dataset, and member `DEMO-202`.
2. Set the notice to **Persistent** and enable human takeover.
3. Start replay and wait for it to pause.
4. Press **Resume** before repair. The request should be rejected.
5. In the existing Chromium session, click **Resolve notice manually**.
6. Press **Resume** again.

During takeover, the demo shows the manual resolution control in place of the automatic dismissal control.

Resume checks the original single-tab session, policy, requested member-details page, absence of the notice, and one actionable pending link. A repairable checkpoint failure leaves replay paused. Policy violations or loss of the required session end the run.

One takeover is allowed per run, with a default timeout of 180 seconds. Cancellation or expiry returns a structured failure with a specific code and recovery event.

Discovery can also request one handoff when its initial eight-step budget is exhausted or an actionable target times out. Resume requires the original URL, unchanged member input, one open page, no blocked traffic, and no workflow-advancing manual action. Only the known notice controls may be used. A validated Resume grants one additional eight-step window with continuous step numbering; human actions are not saved in the capability. Policy violations, model failures, malformed actions, lost sessions, a second blockage, cancellation, timeout, or an invalid Resume stop discovery.

The evidence records control transfers and up to 100 categorized manual actions without entered values. Recovery from other failure types is not implemented.

## Results and evidence

Known replay outcomes use these result types:

| Status | Meaning |
| --- | --- |
| `success` | UI verification passed. The result contains verified member, account, balance, and currency outputs plus a recovery-event list. |
| `business_outcome` | The supported missing-member outcome was detected: `member_not_found`, with any recovery events. |
| `failure` | Replay stopped with a code, step when available, expected condition, safe observations, error type, and recovery events. |

`replay_capability` wraps those outcomes in a job result. A `replay_result`
job contains one of the three capability outcomes above. A `job_failure`
reports a capability-load, evidence, environment, or unexpected orchestration
error with a safe code, stage, and exception type. Failures after logging starts
retain their run ID and end with `run_failed`; pre-log failures use a null run ID.

Clean runs return an empty `recovery_events` list. Notice handling records automatic recovery, human recovery, exhaustion, cancellation, or timeout. Terminal codes distinguish `recovery_exhausted`, `human_takeover_cancelled`, and `human_takeover_timed_out` from other hard failures.

Outputs come from the displayed UI, separately from the model summary. Ordinary replay job exceptions are returned through the structured job-failure contract; process-control exceptions such as cancellation are not converted.

### Saved evidence

| Location | Contents |
| --- | --- |
| `evidence/capabilities/*.json` | Versioned workflows with reusable input/output contracts, recorded controls, and checkpoints. |
| `evidence/runs/*.jsonl` | Discovery and replay events, including the target URL, verification, recovery, and handoff. |
| `evidence/runs/*.failure.json` | Replay or discovery failure details and structural capture, or a marker that the snapshot was unavailable. |

New run logs use evidence schema `1.2` and record the allowlisted target URL. Discovery action proposals include the provider, returned model name, response ID, and nullable input, output, and total token counts. Existing `1.0` and `1.1` logs remain supported and are not rewritten.

Action-step events now include fixed purposes such as `enter_member_id` and `open_savings_account`. Verification, recovery, and selected handoff events also include purposes. These describe application-defined intent without storing model-generated reasoning. Older logs may have no purpose field.

Saved JSONL events omit prompts, raw page observations, model response content, summaries, and entered values. When a browser page is available, failure capture uses schema `1.1` and saves up to 200 DOM nodes with structural information and known-control counts, excluding page text, field values, and raw attributes. Archived `1.0` captures remain readable. If a capture cannot be written, the run records that failure evidence is unavailable.

Terminal diagnostics contain only safe run metadata, redacted identifiers, and fixed action, purpose, status, recovery, and failure fields. Caller-facing results and the authenticated local console continue to expose the verified task output, but raw member IDs, entered values, page observations, model text, and verified banking outputs are not printed to the terminal.

### Evidence examples

The following fresh canonical set demonstrates discovery for `DEMO-101` and replay outcomes for new inputs and injected notice conditions:

- [Saved capability](evidence/capabilities/get_savings_balance_a38e407f-9c75-4cd0-9a78-868e7e474803.json)
- [Genuine model-driven discovery](evidence/runs/a38e407f-9c75-4cd0-9a78-868e7e474803.jsonl)
- [Clean replay with another member](evidence/runs/161cb2b8-a7ec-4376-bf75-5965e10f21a5.jsonl)
- [Successful automatic notice recovery](evidence/runs/1292ed0d-2ca9-4a78-ab58-d5f5ca83a24d.jsonl)
- [Rejected Resume, manual repair, and successful continuation](evidence/runs/6753ef5d-e557-4308-b4ea-4953ca1acc65.jsonl)
- [Missing-member business outcome](evidence/runs/e902227f-d552-4b3a-b50a-fd1913148815.jsonl)
- [Persistent-notice hard failure](evidence/runs/7782e4bf-8ff8-4012-b693-a31ee1bcb800.jsonl) and its [structural failure capture](evidence/runs/7782e4bf-8ff8-4012-b693-a31ee1bcb800.failure.json)

The workflow checks passed for verified outputs, capability provenance, event order, recovery events, and saved purpose labels. Every replay in this set references the discovery run through `source_run_id`.

The default test suite also passed all eight modules: capability contracts, policy, failure-evidence privacy, browser interaction and verification, business outcomes, workflows, end-to-end error contracts, and terminal-output privacy.

Older artifacts and logs remain in place for compatibility checks and historical evidence.

## Project structure

### Automation

| File | Purpose |
| --- | --- |
| `automation/jobs.py` | Shared job functions, task models, capability loading, and browser/server setup. |
| `automation/discovery.py` | Runs the model loop and collects actions and checkpoints. |
| `automation/planner.py` | Sends the goal and page observation to the model for a typed action. |
| `automation/observation.py` | Reads the page URL, title, and accessibility snapshot. |
| `automation/actions.py` | Defines action models and strict validation settings. |
| `automation/executor.py` | Checks policy, locates controls, and performs actions. |
| `automation/recording.py` | Records actions, replaces member-specific values with input references, and assigns action purposes. |
| `automation/capability.py` | Defines capability metadata, embedded contracts, legacy loading, templates, actions, and checkpoints. |
| `automation/replay.py` | Runs saved actions, checks progress, and handles known outcomes and failures. |
| `automation/results.py` | Defines replay result formats. |
| `automation/verification.py` | Checks the account page and reads its displayed values. |
| `automation/business_outcomes.py` | Detects the missing-member outcome. |
| `automation/recovery.py` | Attempts dismissal of the known service notice. |
| `automation/policy.py` | Loads policy and checks URLs, methods, and actions. |
| `automation/network.py` | Checks intercepted browser requests, blocks disallowed requests and redirects, and closes every WebSocket before connection. |
| `automation/handoff.py` | Pauses for human repair, records manual actions, and validates Resume. |
| `automation/takeover_controls.py` | Provides queued Resume and Cancel controls for console and CLI takeover. |
| `automation/evidence.py` | Writes event logs and attempts structural failure capture. |
| `automation/console.py` | Provides console endpoints, job queues, run state, and evidence access. |
| `automation/__init__.py` | Marks the directory as a Python package. |

### Interfaces, demo, and configuration

| File or directory | Purpose |
| --- | --- |
| `automation/templates/console.html` | Main operator interface. |
| `automation/templates/operator_panel.html` | Temporary CLI takeover interface. |
| `demo_app/app.py` | Serves the selected dataset through banking demo pages. |
| `demo_app/server.py` | Starts and stops the demo server for jobs. |
| `demo_app/data/*.json` | Synthetic records, editable separately from application code. |
| `demo_app/templates/search.html` | Member search and results. |
| `demo_app/templates/member.html` | Member details, account links, and notices. |
| `demo_app/templates/account.html` | Account balance and currency. |
| `config/policy.json` | Default action and request permissions. |

### Entry points and supporting files

| File or directory | Purpose |
| --- | --- |
| `scripts/run_console.py` | Starts the operator console. |
| `scripts/run_discovery.py` | Starts discovery from the command line. |
| `scripts/replay.py` | Starts replay from the command line. |
| `scripts/demo.py` | Demonstrates discovery followed by replay with another member. |
| `scripts/observe_page.py` | Helps inspect page observations during development. |
| `scripts/run_tests.py` | Runs the test modules and reports results. |
| `tests/` | Automated checks and failure fixtures. |
| `evidence/` | Saved capabilities, logs, and failure evidence. |
| `requirements.txt` | Pinned project dependencies. |
| `pyrightconfig.json` | Python version and virtual-environment settings for Pyright and Pylance. |
| `REPORT.md` | Design decisions, tradeoffs, limits, and extension plans. |

## Testing

Stop the console and any separately running demo server before testing so port `8000` is available.

### Full suite without model calls

```bash
python -m scripts.run_tests
```

The runner uses separate processes for test modules and manages demo servers where needed. Workflow checks require an existing `get_savings_balance` capability.

### Include live discovery

```bash
python -m scripts.run_tests --live-discovery
```

This requires the configured API key. It adds fresh discovery, then replays that capability with another member and tests persistent-notice takeover.

### Workflow checks alone

```bash
python -m tests.test_workflows
```

To generate fresh discovery and replay evidence:

```bash
python -m tests.test_workflows --live-discovery
```

To replay a specific archived capability, use its filename without `.json`:

```bash
python -m tests.test_workflows --capability-id get_savings_balance_a38e407f-9c75-4cd0-9a78-868e7e474803
```

### Automated checks

| Test module | Coverage |
| --- | --- |
| `tests/test_capabilities.py` | Embedded contracts, target validation, JSON round-tripping, and read-only legacy compatibility. |
| `tests/test_policy.py` | Configurable permissions, blocked destinations and methods, and risky-button restrictions. |
| `tests/test_failure_evidence.py` | Structural capture and absence of named sensitive values in a test fixture. |
| `tests/test_browser.py` | Browser actions, result verification, rejection of the wrong member, and request blocking. |
| `tests/test_business_outcomes.py` | Existing-member and missing-member detection. |
| `tests/test_workflows.py` | Optional live discovery, replay, provenance, event order, saved purposes, older-event compatibility, and validated takeover. |
| `tests/test_error_contracts.py` | End-to-end replay outcomes, recovery events, discovery escalation, structural failure capture, model metadata, and WebSocket blocking. |

The takeover tests use scripted operator controls to request Resume before repair, click the real manual-resolution button, and request Resume again. They also cover cancellation, timeout, rejected discovery Resume, and bounded discovery continuation. They exercise production handoff validation and manual-action recording, but do not drive the console through a browser.

### Manual checks

The console was also checked for:

- Default and alternate datasets.
- Missing-member outcomes.
- Automatic dismissal of a notice.
- Persistent-notice failure with takeover disabled.
- Rejected Resume before repair and successful Resume afterward.
- Cancellation and takeover expiry.
- Refresh during takeover, restored controls, and prevention of a second active run.

Saved evidence was inspected alongside the console results. Earlier manual checks remain useful, although their logs may lack the newer purpose field.

## Limitations and design documentation

- **One task:** Goals are limited to the savings-balance lookup.
- **One application:** Other web interfaces, desktop apps, and real banking systems are not supported.
- **Limited recovery:** Automatic repair and human takeover cover the demonstrated service-notice condition; discovery handoff is limited to one additional eight-step window.
- **Local operation:** One active job is supported. There are no user accounts, remote co-browsing, or job recovery after a restart.
- **Safety limits:** Policy checks and intercepted-request restrictions are not an operating-system sandbox. Discovery sends synthetic page observations to the model.

See [REPORT.md](REPORT.md) for design decisions, error handling, human takeover, safety limits, and plans for more tasks, other UI surfaces, and reuse across institutions.
