"""Offline suite. A fake `claude` CLI stands in for the models: it follows a plan for the coder and
the scout, and answers the judge's typed questions from a file. Nothing here touches the network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CCL = ROOT / "skills/claude-contract-loop/scripts/ccl.py"
EXAMPLE = ROOT / "skills/claude-contract-loop/templates/contract.example.json"
sys.path.insert(0, str(CCL.parent))
import ccl  # noqa: E402

FAKE_CLAUDE = r'''
import json, os, subprocess, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                        "saw_openrouter_key": "OPENROUTER_API_KEY" in os.environ}) + "\n")

def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None

if argv[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": not os.environ.get("FAKE_LOGGED_OUT")}))
    sys.exit(0)
if opt("--json-schema"):  # the judge: answer each typed question from the test's answer file
    if os.environ.get("FAKE_JUDGE_FAIL"):
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "rate limited"}))
        sys.exit(1)
    answers = json.load(open(os.environ["FAKE_ANSWERS"]))
    props = json.loads(opt("--json-schema"))["properties"]["answers"]["properties"]
    out = {}
    for qid, sub in props.items():
        if "noul" in sub["properties"]:
            out[qid] = {"noul": answers[qid]}
        else:
            keys = list(sub["properties"]["probabilities"]["properties"])
            out[qid] = {"choice": answers[qid],
                        "probabilities": {k: (0.9 if k == answers[qid] else 0.05) for k in keys}}
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "",
                      "structured_output": {"answers": out}, "total_cost_usd": 0.001}))
    sys.exit(0)
# the coder or the scout: follow the test's plan, one step per call
plan = json.load(open(os.environ["FAKE_PLAN"]))
step = plan.pop(0) if plan else {}
json.dump(plan, open(os.environ["FAKE_PLAN"], "w"))
for path, content in step.get("write", {}).items():
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(content)
if step.get("commit"):
    subprocess.run("git add -A && git commit -qm sneaky", shell=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": step.get("brief", "DONE"), "total_cost_usd": 0.02,
                  "permission_denials": [{"tool_name": "Read"}] if step.get("refused") else []}))
'''

ACC = "import sys\nsys.path.insert(0, '.')\nfrom m import f\nassert f() == 2, 'f() must return 2'\nprint('acceptance ok')\n"
GOOD = {"write": {"m.py": "def f():\n    return 2\n"}}
BAD = {"write": {"m.py": "def f():\n    return 1\n"}}
DEFAULT_ANSWERS = {"in_scope": 0.95, "same": 0.1, "risk": "normal", "meets": 0.9}


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def variadic(argv, name):
    """The values after a variadic option, up to the next option."""
    if name not in argv:
        return []
    out = []
    for item in argv[argv.index(name) + 1:]:
        if item.startswith("--"):
            break
        out.append(item)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.repo = self.tmp / "proj"
        (self.repo / "tests").mkdir(parents=True)
        self.sh("git init -q . && git config user.email t@t && git config user.name t")
        (self.repo / "seed.txt").write_text("seed\n")
        self.sh("git add -A && git commit -qm seed")
        fake = self.tmp / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE)
        roles = self.tmp / "roles.json"
        roles.write_text(json.dumps({"env_allow": ["FAKE_PLAN", "FAKE_LOG", "FAKE_ANSWERS",
                                                   "FAKE_JUDGE_FAIL", "FAKE_LOGGED_OUT"]}))
        self.plan, self.log = self.tmp / "plan.json", self.tmp / "calls.jsonl"
        self.answers_file = self.tmp / "answers.json"
        self.env = dict(os.environ, CLAUDE_CONTRACT_LOOP_STATE=str(self.tmp / "state"),
                        CLAUDE_CONTRACT_LOOP_CLI="%s %s" % (sys.executable, fake),
                        CLAUDE_CONTRACT_LOOP_ROLES=str(roles),
                        FAKE_PLAN=str(self.plan), FAKE_LOG=str(self.log), FAKE_ANSWERS=str(self.answers_file),
                        OPENROUTER_API_KEY="must-not-leak", PYTHONDONTWRITEBYTECODE="1")
        self.set_answers()
        self.plan.write_text("[]")
        rc, out = self.cl("init")
        self.assertEqual(rc, 0, out)
        self.state = next((self.tmp / "state").iterdir())

    def sh(self, cmd):
        r = subprocess.run(cmd, shell=True, cwd=str(self.repo), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r.stdout

    def cl(self, *args, env=None):
        r = subprocess.run([sys.executable, str(CCL), "--repo", str(self.repo)] + list(args),
                           capture_output=True, text=True, env=env or self.env)
        return r.returncode, r.stdout + r.stderr

    def set_answers(self, **over):
        self.answers_file.write_text(json.dumps(dict(DEFAULT_ANSWERS, **over)))

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def agent_calls(self):
        """Coder and scout calls: prompts go after -p, and they carry no --json-schema."""
        return [c for c in self.calls() if c["argv"][:1] == ["-p"] and "--json-schema" not in c["argv"]]

    def task(self, tid="t1"):
        return json.loads((self.state / "state.json").read_text())["tasks"][tid]

    def write_contract(self, tasks, launch=None):
        c = {"goal": "tiny lib", "stack": {"decision": "python", "why": "test"}, "protected": ["tests/**"],
             "launch": launch or {"cmd": "%s -c pass" % sys.executable, "ready": {"exit": 0}, "timeout_s": 10},
             "tasks": tasks}
        (self.state / "contract.json").write_text(json.dumps(c))

    def seal(self, plan, launch=None):
        """One task whose attempts follow `plan` in order, checked by tests/acc.py."""
        (self.repo / "tests/acc.py").write_text(ACC)
        self.write_contract([{"id": "t1", "title": "make f return 2",
                              "spec": "Implement f() in m.py so that it returns 2.",
                              "allowed": ["m.py"], "checks": ["python3 tests/acc.py"]}], launch=launch)
        self.plan.write_text(json.dumps(list(plan)))
        rc, out = self.cl("seal")
        self.assertEqual(rc, 0, out)


class Loop(Base):
    def test_happy_path_commits_and_closes(self):
        self.seal([GOOD])
        rc, out = self.cl("run")
        self.assertEqual(rc, 0, out)
        t = self.task()
        self.assertEqual((t["status"], t["by"], t["attempts"], t["gate"]["verdict"]), ("done", "coder", 1, "accept"))
        self.assertEqual(self.sh("git status --porcelain"), "")
        self.assertIn("ccl(t1)", self.sh("git log --oneline -1"))
        rc, out = self.cl("close")
        self.assertEqual(rc, 0, out)
        report = (self.state / "close.md").read_text()
        self.assertIn("Checks (all tasks, rerun together): PASS", report)
        self.assertIn("- m.py: normal", report)

    def test_every_model_call_is_locked_down(self):
        self.seal([GOOD])
        self.assertEqual(self.cl("run")[0], 0)
        coder = self.agent_calls()[-1]["argv"]
        self.assertEqual(opt(coder, "--tools"), "Read,Edit,Write,Glob,Grep,Bash")
        self.assertEqual(opt(coder, "--allowedTools"), "Read,Edit,Write,Glob,Grep,Bash")
        self.assertEqual(opt(coder, "--permission-mode"), "dontAsk")
        self.assertEqual(opt(coder, "--model"), "haiku")
        for flag in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands", "--safe-mode"):
            self.assertIn(flag, coder)
        self.assertEqual(opt(coder, "--setting-sources"), "project,local")
        deny = variadic(coder, "--disallowedTools")
        for rule in ("Read(**/.env)", "Read(**/.env.*)", "Bash(git commit *)", "Bash(curl *)"):
            self.assertIn(rule, deny)
        self.assertFalse(any(c["saw_openrouter_key"] for c in self.calls()))

    def test_scout_is_read_only_and_writes_brief(self):
        self.plan.write_text(json.dumps([{"brief": "# Brief\nStack: python", "write": {"stray.txt": "x"}}]))
        rc, out = self.cl("scout", "--goal", "tiny lib")
        self.assertEqual(rc, 0, out)
        self.assertIn("Stack: python", (self.state / "brief.md").read_text())
        self.assertFalse((self.repo / "stray.txt").exists())
        scout = self.agent_calls()[-1]["argv"]
        self.assertEqual(opt(scout, "--tools"), "Read,Glob,Grep")
        self.assertEqual(opt(scout, "--allowedTools"), "Read,Glob,Grep")

    def test_guard_reverts_protected_and_out_of_scope_files(self):
        cheat = {"write": {"m.py": "def f():\n    return 1\n", "tests/acc.py": "pass\n", "other.txt": "x"}}
        self.seal([cheat, GOOD])
        rc, out = self.cl("run")
        self.assertEqual(rc, 0, out)
        self.assertIn("f() must return 2", (self.repo / "tests/acc.py").read_text())
        self.assertFalse((self.repo / "other.txt").exists())
        self.assertEqual(self.task()["attempts"], 2)
        prompt = (self.state / "log/t1.a2.prompt.md").read_text()
        self.assertIn("reverted tests/acc.py: protected file", prompt)
        self.assertIn("reverted other.txt: outside the allowed set", prompt)

    def test_same_failure_twice_escalates_then_resolve(self):
        self.seal([BAD, BAD, BAD])
        rc, out = self.cl("run")
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.task()["attempts"], 2)  # the third attempt is never spent
        packet = (self.state / "escalations/t1.md").read_text()
        self.assertIn("same failure twice", packet)
        self.assertIn("return 1", packet)
        self.assertEqual(self.cl("run")[0], 3)  # refuses to move on past an escalation
        (self.repo / "m.py").write_text("def f():\n    return 2\n")
        rc, out = self.cl("resolve", "t1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.task()["by"], "architect")
        self.assertEqual(self.cl("run")[0], 0)

    def test_scope_gate_reject_escalates_even_when_checks_pass(self):
        self.set_answers(in_scope=0.05)
        self.seal([GOOD])
        rc, out = self.cl("run")
        self.assertEqual(rc, 3, out)
        self.assertIn("scope gate rejected", out)
        self.assertNotIn("ccl(t1)", self.sh("git log --oneline"))

    def test_scope_gate_doubt_commits_with_flag(self):
        self.set_answers(in_scope=0.5)
        self.seal([GOOD])
        self.assertEqual(self.cl("run")[0], 0)
        self.assertTrue(any(f.startswith("doubt") for f in self.task()["flags"]))

    def test_judge_failure_leaves_gates_unjudged_and_flagged(self):
        self.seal([GOOD])
        env = dict(self.env, FAKE_JUDGE_FAIL="1")
        rc, out = self.cl("run", env=env)
        self.assertEqual(rc, 0, out)
        self.assertTrue(any(f.startswith("unjudged") for f in self.task()["flags"]))
        rc, out = self.cl("close", env=env)
        self.assertEqual(rc, 0, out)
        self.assertIn("m.py (unjudged)", (self.state / "close.md").read_text())

    def test_coder_commit_is_folded_back(self):
        self.seal([dict(GOOD, commit=True)])
        rc, out = self.cl("run")
        self.assertEqual(rc, 0, out)
        log = self.sh("git log --oneline")
        self.assertNotIn("sneaky", log)
        self.assertIn("ccl(t1)", log)

    def test_refused_tool_calls_are_flagged(self):
        self.seal([dict(GOOD, refused=True)])
        self.assertEqual(self.cl("run")[0], 0)
        self.assertIn("refused: Read", self.task()["flags"])

    def test_already_green_checks_are_flagged(self):
        (self.repo / "m.py").write_text("def f():\n    return 2\n")
        self.sh("git add -A && git commit -qm impl")  # the implementation landed before the contract
        self.seal([])
        self.assertEqual(self.cl("run")[0], 0)
        t = self.task()
        self.assertEqual(t["by"], "nobody")
        self.assertTrue(any(f.startswith("already-green") for f in t["flags"]))

    def test_seal_refuses_changes_outside_the_protected_checks(self):
        (self.repo / "tests/acc.py").write_text(ACC)
        (self.repo / "scratch.txt").write_text("left over from an attempt\n")
        self.write_contract([{"id": "t1", "title": "t", "spec": "s", "allowed": ["m.py"],
                              "checks": ["python3 tests/acc.py"]}])
        rc, out = self.cl("seal")
        self.assertEqual(rc, 4, out)
        self.assertIn("scratch.txt", out)

    def test_close_fails_when_product_does_not_start(self):
        self.seal([GOOD], launch={"cmd": "%s -c 'raise SystemExit(7)'" % sys.executable,
                                  "ready": {"stdout": "listening"}, "timeout_s": 10})
        self.assertEqual(self.cl("run")[0], 0)
        rc, out = self.cl("close")
        self.assertEqual(rc, 4, out)
        self.assertIn("exited 7 before it was ready", (self.state / "close.md").read_text())

    def test_close_shows_high_risk_diff(self):
        self.set_answers(risk="high")
        self.seal([GOOD])
        self.assertEqual(self.cl("run")[0], 0)
        self.assertEqual(self.cl("close")[0], 0)
        report = (self.state / "close.md").read_text()
        self.assertIn("### m.py (high)", report)
        self.assertIn("+def f():", report)

    def test_launch_ready_on_stdout_and_process_is_stopped(self):
        server = "%s -u -c \"import time; print('listening'); time.sleep(60)\"" % sys.executable
        self.seal([GOOD], launch={"cmd": server, "ready": {"stdout": "listening"}, "timeout_s": 15, "hold_s": 0.2})
        self.assertEqual(self.cl("run")[0], 0)
        rc, out = self.cl("close")
        self.assertEqual(rc, 0, out)
        left = subprocess.run("pgrep -f \"print\\('listening'\\)\"", shell=True, capture_output=True)
        self.assertEqual(left.returncode, 1, "the launched process was left running")

    def test_init_refuses_without_cli_or_login(self):
        rc, out = self.cl("init", env=dict(self.env, CLAUDE_CONTRACT_LOOP_CLI="/nonexistent/claude"))
        self.assertEqual(rc, 4, out)
        self.assertIn("not found", out)
        rc, out = self.cl("init", env=dict(self.env, FAKE_LOGGED_OUT="1"))
        self.assertEqual(rc, 4, out)
        self.assertIn("not logged in", out)


class Units(unittest.TestCase):
    def test_example_contract_is_valid(self):
        self.assertEqual(ccl.validate_contract(json.loads(EXAMPLE.read_text())), [])

    def test_contract_validation_reports_each_problem(self):
        errs = ccl.validate_contract({"goal": "g", "stack": {"decision": "x"},
                                      "launch": {"cmd": "x", "ready": {}},
                                      "tasks": [{"id": "a b", "allowed": [], "checks": ["x"]}]})
        joined = "\n".join(errs)
        for needle in ("stack needs", "launch needs", "id must match", "missing spec", "'allowed' must be"):
            self.assertIn(needle, joined)

    def test_fingerprint_ignores_numbers_and_timings(self):
        self.assertEqual(ccl.fingerprint("took 12.5s at 0xbeef"), ccl.fingerprint("took 98.1s at 0xf00d"))
        self.assertNotEqual(ccl.fingerprint("AssertionError: 2"), ccl.fingerprint("NameError: x"))

    def test_answer_schema_has_one_property_per_question(self):
        s = ccl.answer_schema({"a": {"type": "noul", "criteria": {}},
                               "b": {"type": "choice", "criteria": {"low": "", "high": ""}}})
        props = s["properties"]["answers"]["properties"]
        self.assertEqual(set(props), {"a", "b"})
        self.assertEqual(props["b"]["properties"]["choice"]["enum"], ["low", "high"])

    def test_plain_text_judge_answer_is_parsed_as_fallback(self):
        text = 'Sure:\n```json\n{"answers": {"a": {"noul": 0.5}}}\n```'
        self.assertEqual(ccl.parse_json_text(text), {"answers": {"a": {"noul": 0.5}}})
        self.assertIsNone(ccl.parse_json_text("no json here"))

    def test_agent_env_is_an_allowlist(self):
        saved = dict(os.environ)
        try:
            os.environ.update(GITHUB_TOKEN="x", DATABASE_URL="postgres://y", ANTHROPIC_API_KEY="z",
                              CLAUDE_CODE_OAUTH_TOKEN="w", CLAUDE_CONFIG_DIR="/tmp/cfg")
            env = ccl.agent_env({"env_allow": ["DATABASE_URL"]})
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertEqual(env.get("DATABASE_URL"), "postgres://y")
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
            self.assertEqual(env.get("CLAUDE_CONFIG_DIR"), "/tmp/cfg")
            self.assertIn("PATH", env)
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_stage_image_copies_small_pictures(self):
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "mock.png"
        src.write_bytes(b"\x89PNG small")
        self.assertEqual(ccl.stage_image(src, tmp / "dest").read_bytes(), b"\x89PNG small")


if __name__ == "__main__":
    unittest.main()
