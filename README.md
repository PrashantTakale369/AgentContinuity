<p align="center">
  <img src="repo_img/image.png" alt="AgentContinuity logo" width="900">
</p>

# AgentContinuity

Continue your coding work when you switch agents.

## What we are trying to do

When you move from one coding agent to another, you often have to explain your
task again. The new agent needs to know what you asked for, what changed, what
failed, and what still needs to be done.

AgentContinuity aims to carry that useful context with you. Its `agenthandoff`
command helps you find an existing conversation and prepare it for another
agent, together with the current state of the original project.

For example, you are working in Codex on a session called `AgentContinuity`.
You open Claude and type `/agenthandoff`. You can find that Codex session even
if Claude is open in a different directory. Select it to prepare the context
for continuing the work.

## Get started

You need Python 3.11 or later, Git, and an installed agent that you have signed
into. This version supports Linux, macOS, and Windows through WSL.

Download and extract an AgentHandoff release ZIP, or clone this repository.
Inside its folder, run:

```bash
python3 install.py
agenthandoff init
```

Follow the installer's PATH instruction if it prints one. Setup detects
supported agents and adds a personal skill that works across your projects.
Restart your agent if the new skill does not appear. Run setup again after
installing another supported agent. Install once in each machine or VM where
you want to use it.

| Agent | Command inside the agent |
| --- | --- |
| Claude Code | `/agenthandoff` |
| Codex CLI or IDE extension | `$agenthandoff`, or choose it through `/skills` |

## How you use it

1. Open the agent you want to continue with and invoke its handoff skill.
2. Look through the numbered sessions, including sessions from other projects.
3. Choose the conversation you want to continue.
4. AgentHandoff reads its available history, checks the original project, and
   prepares context for the receiving agent.

If that project is already open, the agent can use the prepared context there.
If another project is open, the skill gives you a terminal command that starts
the continuation in the correct folder. Claude also supports switching folders
with `/cd`. Stop editing in the source agent before continuing elsewhere.

You can also browse sessions directly from any terminal directory:

```bash
agenthandoff sessions
agenthandoff sessions --query "AgentContinuity"
agenthandoff resume 1 --listing LISTING_ID --target claude
```

Use the listing ID printed with the session list. It keeps your selected number
tied to the conversation you actually saw. Use `agenthandoff sessions --all`
to show every accessible session.

## What gets carried over

The handoff includes the task objective, user requirements, relevant conversation
excerpts, project state, and available verification results. An assistant saying
that tests passed is kept separate from an observed test result.

Data stays on your machine. Native conversations are preserved. Evidence for a
selected project is stored in its `.agentcontinuity/` folder, which should be
ignored by Git. AgentHandoff prepares context locally without calling a model;
the receiving agent uses its own account and permissions.

| Local location | Purpose |
| --- | --- |
| `~/.local/bin/agenthandoff` | Command installed by `install.py` |
| `~/.local/share/agenthandoff/` | Installed application, setup configuration, and saved session listings |
| `~/.agents/skills/agenthandoff/` | Personal Codex skill |
| `~/.claude/skills/agenthandoff/` | Personal Claude skill with the default Claude configuration |
| `<original project>/.agentcontinuity/` | Captured evidence, task state, and prepared handoffs |

These are the default locations. `AGENTHANDOFF_HOME` changes the configuration
and listing directory; `XDG_DATA_HOME` also affects that default. `CODEX_HOME`
and `CLAUDE_CONFIG_DIR` control where native sessions are discovered. For custom
session paths, use `agenthandoff init --codex-root PATH --claude-root PATH`.

If a session is missing, run `agenthandoff doctor` and check the session roots.
Only conversations accessible to the current user can be listed. Session
activity timestamps do not prove that an agent is currently running.

## Benchmark results

The synthetic suite covers eight scenarios in both directions: Codex to Claude
and Claude to Codex. Each scenario uses histories with 12, 40, and 80 unrelated
tool messages. That gives 48 cases per context limit and 144 cases in total.

Checks cover complete capture, preserved user requirements, exact byte limits,
evidence retrieval, and verification status. Oversized mandatory requirements
must be refused rather than silently shortened.

<!-- benchmark-summary:start -->
Measured on 19 October 2026 using Python 3.12.3 on Linux aarch64.

| Context limit | Cases meeting checks | Packets with all clues | Safe refusals | Median build time |
| --- | --- | --- | --- | --- |
| 6,000 bytes | 48 / 48 | 36 / 42 | 6 / 6 | 3.248 ms |
| 24,000 bytes | 48 / 48 | 36 / 42 | 6 / 6 | 6.418 ms |
| 80,000 bytes | 48 / 48 | 36 / 42 | 6 / 6 | 6.503 ms |
<!-- benchmark-summary:end -->

Six packets at each limit intentionally place a clue beyond the tool output
excerpt. Their complete evidence remains retrievable locally, but the packet
alone does not include the clue. Six other cases intentionally exceed the
requirement limit and must return a clear refusal without a partial packet.

The developer guide includes a comparison with a newest first message window
under the same byte limits. The small benchmark measures local context
preparation and evidence retention. It does not measure coding quality, model
response time, task completion, or full handoff time. Timings vary by machine.

The [complete results](developer_guide/workflow_testing/benchmark_results.json)
include every case and source file hashes. The
[developer guide](developer_guide/Readme.md#benchmark-cases) explains how to
reproduce the measurements and interpret the results.

## Folder structure

```text
AgentContinuity/
├── src/                         Application, installed as agentcontinuity
│   ├── agents/                  Agent definitions and shared discovery
│   ├── cli/                     Terminal commands and argument parsing
│   ├── context/                 Task projection and context packet selection
│   ├── core/                    Errors, redaction, and file safety
│   ├── integrations/            Shared handoff skill and MCP interface
│   ├── services/                Capture, setup, resume, launch, and verification
│   ├── storage/                 Local evidence ledger and schema
│   └── workspace/               Git state and configuration
├── benchmarks/
│   └── selection.py             Synthetic handoff scenarios and measurements
├── developer_guide/
│   ├── Readme.md                Contribution steps and benchmark methodology
│   ├── workflow_testing/        Tests, synthetic fixtures, and recorded results
│   └── ci_validation/
│       └── checks.py            Final local and CI validation command
├── scripts/
│   └── build_release.py         Build the downloadable release ZIP
├── .github/                     GitHub Actions and pull request template
├── agenthandoff                 Run the CLI directly from a checkout
├── install.py                   Install the standalone application
├── pyproject.toml               Python packaging and development tools
├── README.md                    User setup and project overview
└── LICENSE                      Project license
```

To support another agent, start in `src/agents/`. The
[developer guide](developer_guide/Readme.md#adding-an-agent) explains the shared
contract and includes a working example. No central agent list needs editing.

## Contributing

Agent definitions live in `src/agents/`. A new agent file describes its session
format, default session location, and launch behavior. Shared code handles
discovery, storage, and handoff preparation.

The [developer guide](developer_guide/Readme.md) explains how to clone the repo,
test the existing workflow, make changes, and validate them before opening a
pull request. It also includes instructions for adding an agent.

To install the checkout for development and run its tests:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 -B -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -B developer_guide/workflow_testing/run.py
.venv/bin/python -B developer_guide/ci_validation/checks.py
```

Run workflow tests before editing. After your changes, run them again and finish
with CI validation. Open a pull request once all checks pass. GitHub Actions runs
the same validation for pull requests.

## Current scope

This version works with accessible native JSONL conversations on the same
machine and with original Git projects that still exist. Hidden reasoning,
database only sessions, and sharing between separate machines are outside the
current scope. Available history and context limits can affect what a handoff
contains.

AgentHandoff prepares a continuation from available evidence. It does not move
the source agent's internal state or guarantee that the receiving agent will
complete the task correctly.

The project is still being developed. See [LICENSE](LICENSE) for its license.
