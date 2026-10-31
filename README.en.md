# DualPath

English | [中文](README.md)

Failure-first, evidence-driven task review and verification for AI agents.

v0.2 no longer describes model guesses with probabilities or risk levels. Instead, every failure item should carry a quote that can be checked against the source material and a signal that can be tested:

- `dualpath review`: for general tasks. It writes a draft first, then independently lists failure modes and reviews them one by one.
- `dualpath verify`: for Python functions. It independently proposes counterexamples, blind-reviews the expected values, and runs them in an isolated sandbox.
- `dualpath run`: the legacy dual-path + probability mode. It is kept for compatibility but deprecated, and is planned for removal in v0.3.

The results of the two small experiments are deliberately modest: there is currently no evidence that DualPath or the verification mode beats an ordinary self-check. See the [experiment index](reports/README.md) for the full records.

> Detailed documentation (`docs/*.md`, `reports/*.md`, `CHANGELOG.md`, `CONTRIBUTING.md`) is currently written in Chinese. This page mirrors the Chinese `README.md`.

## Quick start

Requirements: Python 3.10 or newer, and a working DeepSeek API key.

```bash
git clone https://github.com/laozidabo/dualpath.git
cd dualpath
python -m venv venv
venv/bin/python -m pip install -e ".[dev]"

# Option 1: environment variable
export DEEPSEEK_API_KEY="your DeepSeek API key"

# Option 2: put it in .env in the current working directory
# (the program loads it at startup)
```

```bash
venv/bin/dualpath review "Design and implement a command-line todo manager for an open-source Python project"
```

`review` and `verify` use `deepseek-flash` by default; override it with `--model` or environment variables. At startup the program reads `.env` from the **current working directory** and only fills in missing environment variables; it never overrides variables already set in the shell. Do not put real keys on the command line or commit them to the repository; see [SECURITY.md](SECURITY.md).

## review: a failure list for general tasks

```bash
venv/bin/dualpath review "Write an error-handling spec for the following API: ..." \
  --context docs/api.md \
  --context examples/response.json \
  --max-findings 5 \
  --json review.json \
  --markdown review.md
```

A review run goes through four steps:

1. The draft line completes the task directly.
2. The failure-probe line reads only the task and the material passed via `--context`, and proposes at most `--max-findings` concrete failure modes.
3. An independent review line decides whether each failure mode is supported by the source text and whether its check is usable, and returns `supported`, `ambiguous` or `rejected`.
4. Only supported failure modes are handed to the repair line and merged into the final result.

A review makes at most 4 model calls (draft, probe, review, and one repair call only when a supported failure mode exists). It makes 3 calls when no failure mode is supported, and 2 calls when the probe returns an empty list. If the probe returns malformed JSON it is asked once more, so the worst case is 5 calls. By default it produces at most 5 findings and never outputs probabilities, percentages or risk levels.

### Parameters

| Parameter | Description |
| --- | --- |
| `--context FILE` | Additional task material; repeatable. At most 32 KiB per file and 64 KiB in total. A finding's `rule_quote` must appear in the task or in this material. |
| `--max-findings N` | Maximum number of failure modes to keep. Default 5, allowed range 1-10. |
| `--provider` | `deepseek` (default) or `openai-compatible`. |
| `--model` | Model name. |
| `--api-key` | Not recommended; prefer environment variables or `.env`. |
| `--base-url` | Override the API base URL. |
| `--json PATH` | Save the full record (task, draft, findings, final output, call count). |
| `--markdown PATH` | Save a human-readable version. |
| `--quiet` | Hide progress output. |

### Output and statuses

Every finding contains: `id`, `title`, `mechanism`, `trigger`, `rule_quote`, `rule_source`, `check_kind`, `check`, `expected_signal`, `status` and `review_reason`. Specifically:

- `rule_quote` must be a continuous fragment of the task or of a `--context` file (compared after whitespace normalization); the program validates this.
- `check_kind` is `executable` (a check that can be run) or `manual` (requires human confirmation).
- `status` is `supported` (the source text supports it and the check is usable), `ambiguous` (the rules are ambiguous) or `rejected` (unsupported or unusable).

Run statuses:

| Status | Meaning |
| --- | --- |
| `completed` | Review finished and produced a final result. |
| `no_findings` | No failure mode met the evidence requirement; the draft is used. |
| `draft_only` | No failure mode was supported; the draft is used. |
| `probe_failed` | The failure-probe stage was unavailable; the draft is kept. |
| `review_failed` | The review stage was unavailable; the draft is kept. |
| `model_failed` | Even the draft stage was unavailable; there is no final result. |

Exit codes: `0` means the flow completed (`completed`, `no_findings`, `draft_only`); `2` means a failed or degraded run (`probe_failed`, `review_failed`, `model_failed`, input or environment errors); `130` means the user cancelled. See [docs/REVIEW.md](docs/REVIEW.md) for the detailed schema.

`no_findings` does not mean the task is correct, and `supported` does not mean proven. Review is an inspection tool, not a correctness proof.

## verify: executable evidence for Python functions

`verify` targets code tasks: for a single Python function it independently proposes concrete counterexamples, blind-reviews the expected values, runs them in an isolated bubblewrap sandbox, and only repairs the draft once a failure has been observed.

It requires Python 3.10+, Linux, `/usr/bin/python3`, bubblewrap, a kernel configuration that allows user-namespace isolation, and `DEEPSEEK_API_KEY`. If the sandbox is unavailable, it stops instead of falling back to running on the host.

```bash
venv/bin/dualpath verify \
  "Implement abs_value(x) where x is an integer. Return the absolute value." \
  --function abs_value --budget 0.30 --output verify-runs/example
```

The output directory must be new so old results are never overwritten. `result.json` records every status, counterexample and execution observation; `solution.py` is the code that was finally kept; `ledger.json` records requests, tokens and the conservative cost estimate. Exit codes: `0` when all accepted checks pass; `2` when verification is incomplete, there is no usable check, the repair does not pass, or startup fails; `130` when cancelled. Network requests are not retried automatically, and the budget is tracked per output directory.

See [docs/VERIFIED.md](docs/VERIFIED.md) for the full flow, budget details, the experimental `--interaction-focus` option and the limits.

## Experimental results

Both experiments are small, self-built samples with a single run per task. They neither support claims of general superiority nor yield significance.

| Experiment | Direct answer | Ordinary self-check | Improved method |
| --- | ---: | ---: | ---: |
| Round 1: 30 Python function tasks | 26/30 | 5-round self-review: 28/30 | original DualPath: 28/30 |
| Round 2: 8 Python function tasks | 7/8 | 1-round self-check: 8/8 | verification mode: 7/8 |

In round 1, the 5-round self-review and the original DualPath passed exactly the same tasks; DualPath's estimated cost was about 22% higher, and no unique benefit was observed. In round 2, the 1-round self-check passed 8/8; the 3-round self-check passed 6/6 on the six tasks that had results (two were missing due to API errors/truncation), which cannot be compared directly against the 8-task denominator; the verification mode passed 7/8, and the one it missed (V008) was fixed by the 1-round self-check. Neither round provides evidence that DualPath or the verification mode beats an ordinary self-check.

The cumulative conservative ledger across both rounds is CNY 4.74333148, including reservations for failed requests. That is a local estimate, not a provider bill. Raw records, frozen protocols, offline reproduction and per-task analysis are in [reports/README.md](reports/README.md).

## Legacy: the deprecated run mode

`dualpath run` is the v0.1 dual-path execution mode: line one solves the task directly, line two explores error paths, problems are graded by model-estimated probability, and the two lines converge on a final result. v0.2 keeps its behavior for compatibility, but prints a deprecation warning to stderr at startup and plans to remove it in v0.3.

```bash
# Still works, but not recommended for new work
venv/bin/dualpath run "your task"
```

| Old usage | Migration in v0.2 |
| --- | --- |
| `dualpath run "general task"` | `dualpath review "general task"` |
| `run --max-problems 5` | `review --max-findings 5` (different semantics: no probabilities or levels) |
| `run --json/--markdown` | `review --json/--markdown` |
| probability-graded display in `run` | no longer provided; review groups findings by review status only |
| `run --search-results`, `run --no-search` | no longer provided; review does not search the web, evidence comes from the task and `--context` |
| code/function tasks | `dualpath verify "function spec" --function NAME ...` |

The old output fields of `run` remain compatible; v0.2 additionally records `status` (`completed` or `degraded`) and `failures`, so degraded runs are no longer presented as successes.

## Configuration

| Variable | Description |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek API key. |
| `DEEPSEEK_MODEL` | DeepSeek model name, default `deepseek-flash`. |
| `DEEPSEEK_BASE_URL` | Override the DeepSeek endpoint. |
| `DUALPATH_PROVIDER` | Default provider, default `deepseek`. |
| `DUALPATH_API_KEY` / `OPENAI_API_KEY` | Key for OpenAI-compatible endpoints. |
| `DUALPATH_BASE_URL` / `OPENAI_BASE_URL` | OpenAI-compatible endpoint URL. |
| `DUALPATH_MODEL` / `OPENAI_MODEL` | Model name; `DUALPATH_MODEL` applies to both providers and takes precedence over `DEEPSEEK_MODEL`. |

`.env` loading only fills missing variables: anything already set in the shell wins. Model names and prices follow the [official DeepSeek API documentation](https://api-docs.deepseek.com/).

## Development

```bash
venv/bin/python -m pip install -e ".[dev]"
venv/bin/pytest -q
venv/bin/ruff check .
venv/bin/python examples/verify_offline.py   # offline demo, no API calls; requires bubblewrap
```

Contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md): evidence first, never present a model judgement as a correctness proof, no probability marketing, and keep tests and docs in sync.

## Limitations

- Both experiments use self-built tasks, not a public benchmark, and no independent third party wrote the tasks.
- Only a single model and a single sample were tested; there is no cross-model, repeated-run or significance evidence.
- The coverage of `review` findings is bounded by the model's ability to discover failures; `rule_quote` only guarantees that the quote appears in the material, not that the rule itself is correct or complete.
- `verify` only covers finite counterexamples and the listed exception types; passing the checks does not mean the implementation is correct overall.
- The closed loop (draft -> failure probe -> review -> repair) can organize and expose problems, but it cannot introduce facts that are not in the task.
- Run records contain the original task text and model output, which may be sensitive; treat them as private material.

## Repository layout

```text
src/dualpath/
├── cli.py            # command-line entry point
├── review.py         # review: draft, failure probe, review, repair
├── verification.py   # verify: counterexamples, blind review, isolated execution, repair
├── sandbox.py        # bubblewrap isolation
├── budgeted.py       # budget ledger and cost protection
├── orchestrator.py   # legacy run dual-path orchestration
├── paths.py          # legacy run direct path and error path
├── models.py         # data models
├── prompts.py        # stage prompts
├── llm.py            # model interface, timeouts, JSON parsing
├── reporting.py      # console output
└── search.py         # legacy run search interface
docs/                 # REVIEW / DESIGN / VERIFIED docs (Chinese)
reports/              # experiment conclusions and index (Chinese)
experiments/          # frozen tasks, protocols and analysis scripts
tests/                # unit and isolated-execution tests
examples/             # offline demos
artifacts/            # historical archives and checksums
```

## License

MIT License. For security issues, please report privately as described in [SECURITY.md](SECURITY.md).
