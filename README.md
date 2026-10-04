# smartBank

## LLM banking UI discovery and deterministic replay

Some business applications have no integration API, so automation must use their UI. smartBank demonstrates how an LLM can discover a workflow and save it for reuse without further model calls.

The implemented task retrieves a member’s available savings balance and currency from a local banking demo using synthetic records. Discovery produces a validated JSON capability containing reusable actions, input and output schemas, input references, and checkpoints. Replay follows those actions with another member ID and checks the displayed result.

An operator console provides discovery, replay, dataset selection, results, and run history. When a persistent service notice blocks the supported replay workflow, a human can repair the same browser session and request Resume.

## Features and scope

- **Discovery:** Accepts a natural-language goal, an allowlisted target URL, and a member ID. The model reads the page and proposes actions within an eight-decision limit.
- **Reusable capabilities:** Saves verified workflows as versioned JSON files with self-contained input and output contracts.
- **Replay:** Runs saved workflows with new inputs without model calls.
- **Clear outcomes:** Separates success, missing-member results, and handled execution failures.
- **Recovery and takeover:** Attempts one automatic notice dismissal, then allows human repair when enabled.
- **Operator console:** Provides one interface for running tasks and inspecting evidence.
- **Policy and evidence:** Checks permitted actions and requests, records action purposes, and attempts structural failure capture.

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

The default policy is in `config/policy.json`. It defines permitted origins, paths, request methods, action types, fields, and buttons. Configured risky button names remain blocked even if included in the permitted-button list.

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
5. Enter this goal: “Find this member’s available savings balance and report its currency.”
6. Start the run.

A visible Chromium browser opens. After UI verification succeeds, the console displays the model summary, verified output, run ID, and capability ID.

Each successful discovery saves:

```text
evidence/capabilities/get_savings_balance_<run-id>.json
```

It also updates `get_savings_balance.json`, the convenience alias for the latest successful recording.

New recordings use capability schema `1.2`. Their embedded input and output JSON Schemas are generated from the runtime Pydantic models and validated when loaded. Existing `1.1` artifacts remain supported through an in-memory compatibility upgrade; loading them does not modify their files.

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

One takeover is allowed per run, with a default timeout of 180 seconds. Cancellation or expiry returns a structured failure.

The evidence records control transfers and up to 100 categorized manual actions without entered values. General escalation during discovery and recovery from other failure types are not implemented.

## Results and evidence

Known replay outcomes use these result types:

| Status | Meaning |
| --- | --- |
| `success` | UI verification passed. Output contains member ID, account type, available balance, and currency. |
| `business_outcome` | The supported missing-member outcome was detected: `member_not_found`. |
| `failure` | Replay stopped with a code, step when available, expected condition, safe observations, and error type. |

Outputs come from the displayed UI, separately from the model summary. Unexpected exceptions can propagate from job functions; the console catches them and marks the run as failed.

### Saved evidence

| Location | Contents |
| --- | --- |
| `evidence/capabilities/*.json` | Versioned workflows with reusable input/output contracts, recorded controls, and checkpoints. |
| `evidence/runs/*.jsonl` | Discovery and replay events, including the target URL, verification, recovery, and handoff. |
| `evidence/runs/*.failure.json` | Failure details and structural capture, or a marker that the snapshot was unavailable. |

New run logs use evidence schema `1.1` and record the allowlisted target URL. Existing `1.0` logs remain supported and are not rewritten.

Action-step events now include fixed purposes such as `enter_member_id` and `open_savings_account`. Verification, recovery, and selected handoff events also include purposes. These describe application-defined intent without storing model-generated reasoning. Older logs may have no purpose field.

Saved JSONL events omit raw page observations and model conversations. Failure capture saves up to 200 DOM nodes with structural information and known-control counts, excluding page text, field values, and raw attributes. If the file cannot be written, replay records that failure evidence is unavailable.

Terminal output includes proposed actions and the model summary.

### Evidence examples

The following runs demonstrate discovery for `DEMO-101`, replay for `DEMO-202`, and persistent-notice recovery through scripted human takeover:

- [Saved capability](evidence/capabilities/get_savings_balance_5d9f29de-7bbe-4da0-8f6c-1687f5ee046f.json)
- [Live discovery](evidence/runs/5d9f29de-7bbe-4da0-8f6c-1687f5ee046f.jsonl)
- [Successful replay with another member](evidence/runs/853007f3-4aef-466e-9a21-dbb23f6ed971.jsonl)
- [Rejected Resume, manual repair, and successful continuation](evidence/runs/8c016e6d-02cc-4e83-a146-60ef78038996.jsonl)

The workflow checks passed for verified outputs, capability provenance, event order, and saved purpose labels. Both replay logs reference the discovery run through `source_run_id`.

The default test suite also passed all six modules: capability contracts, policy, failure-evidence privacy, browser interaction and verification, business outcomes, and workflows.

An [earlier missing-member replay](evidence/runs/811880ba-2afa-465b-8f87-c2a25da02b76.jsonl) demonstrates the `member_not_found` business outcome. That run predates purpose logging.

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
| `automation/network.py` | Checks intercepted browser requests and blocks disallowed requests and redirects. |
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
python -m tests.test_workflows --capability-id get_savings_balance_5d9f29de-7bbe-4da0-8f6c-1687f5ee046f
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

The takeover test uses scripted operator controls to request Resume before repair, click the real manual-resolution button, and request Resume again. It exercises handoff validation and manual-action recording, but does not test the console UI.

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
- **Limited recovery:** Automatic repair and human takeover cover the demonstrated service-notice condition.
- **Local operation:** One active job is supported. There are no user accounts, remote co-browsing, or job recovery after a restart.
- **Safety limits:** Policy checks and intercepted-request restrictions are not an operating-system sandbox. Discovery sends synthetic page observations to the model.

See [REPORT.md](REPORT.md) for design decisions, error handling, human takeover, safety limits, and plans for more tasks, other UI surfaces, and reuse across institutions.
