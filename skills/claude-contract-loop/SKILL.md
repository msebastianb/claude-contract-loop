---
name: claude-contract-loop
description: Use when building code with a cheap Claude model under a contract. You write the contract and the acceptance checks; Haiku implements each small task through claude -p, executable checks decide "done", a judge gates scope, and every run ends in a verdict.
---

# Contract loop (Claude Code)

You are the architect: the strongest model selected in this chat. You write the contract and judge the result; a script runs the loop.
Haiku scouts, codes and judges through `claude -p`. Your checks decide "done". The script calls
you back only with a short escalation packet when something needs judgment.

## When to use
- A coding task that splits into 2 or more small tasks, each with a command that proves it done.
- Not for one-line fixes, pure exploration, or work that no command can check.

## Prerequisites
- `claude` on PATH and logged in: `claude auth status` shows `"loggedIn": true`. If not, the user runs `claude auth login`. Never log in on their behalf.
- Python 3.9+, git, and a clean working tree in the target repo.

## Commands
Use the full path every time (shell variables do not survive between tool calls). `--repo` goes before the command:

    python3 ~/.claude/skills/claude-contract-loop/scripts/ccl.py --repo <repo> <command>

| Command | What it does |
|---|---|
| `init` | Checks CLI and login, refuses a dirty tree, cuts branch `ccl/<stamp>`, creates the state dir outside the repo and prints its path |
| `scout --goal "..."` | Read-only Haiku writes `brief.md` (at most 60 lines) |
| `seal` | Validates `contract.json` and commits the acceptance checks |
| `run` | Implements the tasks in order. Exit 0 = all done; exit 3 = escalation packet waiting |
| `resolve <task>` | Accepts your own fix for an escalated task (checks must pass) |
| `run --retry <task>` | Discards the failed attempt and starts that task over |
| `close` | Reruns every check, launches the product, writes `close.md`. Exit 4 = failed |
| `status` | One line per task and the spend so far |

## Procedure
1. **Init, then scout.** Run `init` before you write any file into the repo, or the checks you write become dirt and `init` refuses. Capture the state dir it prints. Run `scout` (skip it on an empty repo) and read only `brief.md`.
2. **Write the contract** at `<state>/contract.json`. Start from `<state>/contract.example.json`.
   - Decide the stack yourself and give a reason.
   - Tasks a mid-level developer finishes in under an hour. Each has `spec`, `allowed` globs and `checks`.
   - Write the acceptance checks yourself, under a `protected` glob. A check must fail now and pass only when the task is really done. Your tokens go here.
   - Checks use `python`, not `curl`, because the coder cannot run `curl`.
   - `launch` is the real start command plus how to know it is up: `http`, `stdout` (regex) or `exit`. No launch, no close.
   - UI tasks get a `ui` block (`url`, `criterion`). An optional `reference` image is shown to the coder and the visual gate.
   - Then run `seal`. It refuses changes outside `protected`, so keep scratch files out of the repo.
3. **Run.** For one small task, run in the foreground. For several, start `run` with the Bash tool's `run_in_background` and read only its output when it finishes. Done when it exits 0, or exits 3 with a packet path.
4. **Escalation (exit 3).** Read the packet: spec, last failure, last diff. Then either fix the working tree and run `resolve <task>`, or rewrite the task, run `seal`, and run `run --retry <task>`. A task flagged `already-green` means its check was vacuous: fix the check.
5. **Close.** Run `close` and read `close.md`: the verdict lines, the task table, and the diffs it selects (high-risk or unjudged files). Report to the user what you read and what you did not. Done when checks and launch are PASS and you have read every diff it lists.
6. **Hand over.** The work sits on branch `ccl/<stamp>`, uncommitted to any remote. Do not merge or push unless the user asks.

## What the loop enforces
- Every model call gets only the tools its role needs: scout Read/Glob/Grep, coder adds Edit/Write/Bash, judge none (the visual gate adds Read). No MCP servers, no transcript on disk, and no CLAUDE.md, skills, hooks or plugins from the user's setup (`--safe-mode`).
- Deny rules refuse `.env` files, ssh/aws keys, curl/wget/ssh, git commit/push/stash/checkout/reset/merge/rebase, and `rm -rf`. Refused calls appear as `refused:` flags and in the next attempt's feedback.
- The model's environment is an allowlist, so keys and tokens in your shell (`ANTHROPIC_API_KEY` included) are not inherited. A project variable the checks need goes in `env_allow` in `roles.json`.
- Changes outside `allowed`, or inside `protected`, are reverted after each attempt. A commit made by the coder is folded back.

## Running it well
- Tokens are the cost. Batch `init`, contract writing and `seal` into few calls; read only what each step asks for.
- Haiku 5.5 (`claude-haiku-5-5`) at max effort is the default for scout, coder and judge (`roles.json`). Change a model or effort there, not in the prompts, and say why in one line.
- Deny rules are guardrails, not a sandbox. A shell command can still reach what the rules do not name. For an untrusted repo, run the loop in a container.

## Pitfalls
- The coder starts with no memory of the conversation. Put everything it needs in the task `spec`; it reads the brief and the repo.
- Globs use Python `fnmatch`, where `*` also crosses `/`. Use `tests/**` for a whole tree.
- Gitignored files are invisible to the revert guard.
- The judge returns probabilities, not reasons. `doubt` and `unjudged` are yours to settle. The thresholds (0.8 accept, 0.2 reject) are starting values; tune them in `roles.json` after real runs.
- A check that does not start the product proves nothing. The launch step exists for that.
- If the judge call fails (rate limit, no login), gates are skipped and flagged `unjudged`; the run still goes on, and `close` lists those files.
- Static checks cannot see browser behaviour. A form with `required` and no `novalidate` lets native validation swallow the submit, so a JS handler that shows feedback never runs while every structural check still passes. Demand `novalidate` in the check, or drive the page with a browser.
- `ui.criterion` is judged on the first viewport only: the screenshot is 1280x800 and shows just what is above the fold. A criterion about the pricing table or the footer fails a good landing page.
- Full-page layout and mobile behaviour are not in the screenshot. Check them yourself before you report PASS.
- With a `reference` image the visual gate often lands in doubt, even on a close page, because the model also compares text it cannot match. Settle it yourself, side by side.
- Pictures over 700 kB are shrunk with `sips` (macOS only) before the model sees them.
- The installed copy is not the source. Edit the repo, then run `install.sh`; an edit made only in `~/.claude/skills` is overwritten.

## Verification
- `python3 -m unittest discover -s tests -v` in the skill's source repo passes offline (a fake `claude` stands in for the models).
- After a run: `git log` on the run branch shows one `ccl(<task>)` commit per task, and `close.md` reports checks PASS and launch PASS.
