# Developer guide

AgentContinuity helps people continue a coding conversation in another agent.
The shared code handles session discovery, context preparation, and local
storage. Agent files describe the formats and commands specific to each agent.

Use this workflow when contributing: clone, test, change, validate, then open a
pull request. Keep the existing folder structure unless your change needs a
different structure.

## 1. Clone and prepare

You need Python 3.11 or later and Git on Linux, macOS, or WSL. Fork
[AgentContinuity](https://github.com/PrashantTakale369/AgentContinuity) on GitHub,
then clone your fork. Replace `YOUR_GITHUB_NAME` below with your account name.

```bash
git clone https://github.com/YOUR_GITHUB_NAME/AgentContinuity.git
cd AgentContinuity
git switch -c improve-handoff
export PYTHONDONTWRITEBYTECODE=1
python3 -B -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The development extra installs the tools needed to build a wheel. Normal
AgentHandoff use has no runtime package dependencies.

## 2. Test the existing workflow

Run this before changing code:

```bash
.venv/bin/python -B developer_guide/workflow_testing/run.py
```

The tests are in [workflow_testing](workflow_testing). They exercise session
capture, context limits, cross directory handoff, agent extensions, command
launching, private local storage, and installation from a release ZIP.

Tests use temporary projects, temporary home directories, synthetic sessions,
and fake agent executables. They do not need Claude or Codex accounts and do not
make model requests. They do not replace a manual check inside a real agent.

If the baseline fails, record the failure before changing code. A passing
baseline makes it easier to identify a regression introduced by your change.

## 3. Make your change

Add a regression test when fixing a bug or adding behavior. Put tests and small
synthetic fixtures in `developer_guide/workflow_testing/`. Keep real conversations,
account information, and private project data out of fixtures.

Run the workflow command again after editing. The runner can also be called
from another directory using its absolute path, after installing this checkout.

### Adding an agent

Start with [example_agent.py](workflow_testing/fixtures/example_agent.py), or
read the existing [Claude](../src/agents/claude.py) and
[Codex](../src/agents/codex.py) implementations. Create
`src/agents/your_agent.py`, subclass [Agent](../src/agents/base.py), and export
an instance named `AGENT`.

| Definition | Purpose |
| --- | --- |
| `name` | A unique agent name used in commands and stored sessions |
| `command` | Executable name, when it differs from the agent name |
| `default_session_root()` | Default directory containing native JSONL sessions |
| `session_identity(record)` | Session ID, absolute project directory, and subagent flag |
| `normalize(record)` | Event kind, text, and whether the record format is unsupported |
| `launch_args(prompt)` | Optional launch arguments when the shared default does not fit |
| `handoff_integration()` | Optional personal skill directory and invocation for `agenthandoff init` |
| `record_title(record)` or `session_titles(root)` | Optional native session names |

Normalized event kinds include `metadata`, `user`, `assistant`, `tool_call`, and
`tool_result`. Preserve unsupported records as `unknown` and set their unsupported
flag to `True`. Tool results must not become user requirements. Session identity
must come from native IDs rather than display names.

Discovery loads new agent modules automatically. No central agent list needs
editing. An integration hook is needed for automatic skill installation in that
agent. Keep any special format or launch behavior in its agent module, and use
the shared storage and handoff code. Existing namespace folders do not need new
`__init__.py` files just to add an agent.

The [agent extension tests](workflow_testing/test_agents.py) demonstrate how a
new module is discovered, captures a session, prepares a handoff, and launches
its executable. Add synthetic tests for your agent's actual format.

## 4. Run final local validation

When the workflow tests pass, run:

```bash
.venv/bin/python -B developer_guide/ci_validation/checks.py
```

The [validation script](ci_validation/checks.py) stops on a failure and returns a
nonzero exit code. It runs these checks in order:

| Check | What it validates |
| --- | --- |
| Development tools | Supported Python, Git, setuptools, and wheel are available |
| Python syntax | Application, installer, test, benchmark, and validation files parse |
| Documentation links | Local README and developer guide links point to existing files |
| Git whitespace | `git diff --check` accepts the tracked changes |
| Wheel installation | Current source builds without downloading dependencies and installs in a fresh virtual environment |
| Installed commands | Both CLI entry points start outside the checkout and the handoff skill is packaged |
| Workflow tests | The full suite passes against the installed wheel, including standalone ZIP installation |
| Synthetic benchmark | All eight scenarios run in both directions at three byte limits, including safe refusal and evidence retrieval |

The validator reads the discovered test count, so new tests are included
automatically.
It also runs 48 synthetic benchmark cases: eight scenarios in both directions
at each of three context limits. Benchmark timings are reported for review;
machine speed is not a pass condition.

To save a report outside the checkout:

```bash
.venv/bin/python -B developer_guide/ci_validation/checks.py --report /tmp/agenthandoff-ci-report.json
```

Builds and installation checks use temporary directories. After development
tools are installed, validation itself needs no network access. Your real
agent setup and conversations are not used by these checks. The public logo is
tracked at [assets/logo.png](../assets/logo.png). Other artwork under `repo_img/`
stays local and is not required in a clean clone.

Run this command locally before opening a pull request. The repository does not
include GitHub Actions workflows. A passing run verifies the environment where
the command was run.

## 5. Open a pull request

Once all checks pass, review your diff, commit your intended files, push your
branch to your fork, and open a pull request against the upstream repository.
Explain the problem, the resulting behavior, and the validation you ran. The
[pull request template](../.github/pull_request_template.md) provides that shape.

Keep private artwork, generated build output, local session data, and virtual
environments out of the commit. Fix local validation failures before asking for
review.

## Benchmark cases

The [benchmark](../benchmarks/selection.py) exercises the native JSONL parsers,
capture, task projection, and packet compiler with deterministic synthetic
sessions. Every scenario runs from Codex to Claude and from Claude to Codex.
Three history sizes, containing 12, 40, and 80 unrelated tool messages, give
six cases per scenario. Eight scenarios at three byte limits give 144 cases.

The scenarios cover early, middle, and recent failures; clues from two files;
an unverified assistant claim; a verification result made stale by editing;
a clue beyond the excerpt limit; and requirements that cannot fit. The stale
verification scenario executes a known local Python command before editing the
temporary project. No model requests or real user conversations are involved.

### Read the results

The [README table](../README.md#benchmark-results) summarizes correctness checks,
complete clue retention in built packets, and safe refusals at each byte limit.
The runner generates `workflow_testing/benchmark_results.json` with per case
checks, source and target agents, required and retained clues, byte counts,
compile times, source hashes, and environment details. This generated report is
not included in the repository.

Correctness checks and inline clue retention answer different questions. A
packet must preserve requirements and stay within its byte limit. Evidence
outside a bounded excerpt can still require retrieval. An expected refusal is
counted as a safe outcome, never as a successfully built packet.

<!-- benchmark-comparison:start -->
At a 24,000 byte limit:

| Scenario | Cases | AgentHandoff includes all clues | Newest first window includes all clues | Cases meeting checks |
| --- | --- | --- | --- | --- |
| Failure before other tool output | 6 | 6 / 6 | 2 / 6 | 6 / 6 |
| Failure in the middle of tool output | 6 | 6 / 6 | 4 / 6 | 6 / 6 |
| Failure at the end of tool output | 6 | 6 / 6 | 6 / 6 | 6 / 6 |
| Two clues from different files | 6 | 6 / 6 | 2 / 6 | 6 / 6 |
| Assistant claims tests passed | 6 | 6 / 6 | 2 / 6 | 6 / 6 |
| Project edited after a passing test | 6 | 6 / 6 | 2 / 6 | 6 / 6 |
| Clue beyond the excerpt limit | 6 | 0 / 6 | 0 / 6 | 6 / 6 |
| Requirements exceed the byte limit | 6 | Not applicable | Not applicable | 6 / 6 |
<!-- benchmark-comparison:end -->

Both selectors keep the same mandatory task state and assistant progress,
use the same 1,800 byte excerpts, and count the complete rendered UTF-8
prompt including metadata and the trust boundary. The comparison adds optional
evidence from newest to oldest and stops when the next item cannot fit.
AgentHandoff ranks optional evidence using the supplied next action and project
files. This is a comparison with a simple selection policy, not a benchmark
against Claude's or Codex's built in memory.

The previous benchmark repeated one case with an early clue and 80 later
messages. That is why its newest message window reported `0/20` at the smaller
limits. The current matrix varies clue position and history length, and shows
recent cases where both selectors retain the clue. A zero in this table means
the complete clue was absent from that packet, not that capture or retrieval
failed. Do not replace missing evidence with a claimed success.

The long output scenario deliberately places its clue after the excerpt limit.
Both selectors can miss it even when the total byte budget increases, because
the per event excerpt limit still applies. The suite checks retrieval of the
full stored evidence by ID. In an actual handoff, inspect the packet's omission
manifest and use `agentcontinuity evidence ID --max-bytes 16000` from the
original project when an excerpt is insufficient.

These fixtures use a supplied next action related to the failure. They do not
establish performance when that action is unknown, when semantic relevance is
ambiguous, or on real coding tasks. They measure neither model quality nor task
completion. Compile timings exclude capture, verification, baseline selection,
and full evidence retrieval. This small deterministic suite does not provide
statistical confidence intervals or a production latency estimate.

### Reproduce the measurements

Run all 144 cases after development setup:

```bash
.venv/bin/python -B developer_guide/workflow_testing/benchmark.py
```

This refreshes the raw results. To publish fresh measurements and regenerate
both documentation tables from those exact results:

```bash
.venv/bin/python -B developer_guide/workflow_testing/benchmark.py --update-docs
```

Use `--output /tmp/agenthandoff-benchmark.json` for an exploratory run that should
not replace recorded results. Use `--repeats 1` for a shorter run containing one
history size. To inspect every case at one byte limit:

```bash
.venv/bin/python -B -m benchmarks.selection --repeats 3 --budget-bytes 24000
```

The runner verifies that the installed parser and context implementation match
the checkout being measured. A failed correctness check returns a nonzero exit
code. There is no minimum timing requirement or requirement for the simple
newest first baseline to achieve a chosen score.
