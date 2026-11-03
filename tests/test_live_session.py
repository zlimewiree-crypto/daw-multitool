"""Claude → Codex hand-over: the hook keeps notes/LIVE_SESSION.md current and holds Claude's automatic continue
when someone else changed files. Runs on a temporary folder; no real notes, notifications or Claude."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("live_session", ROOT / "scripts/live_session.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

RESUME = ("Your claude.ai usage limit has reset. Continue the task you were working on when the limit was reached; "
          "do not repeat work that is already complete.")  # Claude Code's own wording (2.1.314)


class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name in ("notes/HANDOFF.md", "multitool/app.py", "tests/test_app.py"):
            self.touch(name, 0)
        self.alerts = []
        patcher = patch.object(live, "notify", lambda title, text: self.alerts.append(title))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 1_000_000.0

    def touch(self, name, when):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        os.utime(path, (when, when))

    def send(self, event, after=1, **fields):
        self.now += after
        return live.run({"hook_event_name": event, **fields}, root=self.root, now=self.now)

    def edit(self, name):
        self.touch(name, self.now + 0.5)  # Claude saves the file, then PostToolUse runs
        self.send("PostToolUse", tool_name="Edit", tool_input={"file_path": str(self.root / name)})

    def note(self):
        return (self.root / "notes/LIVE_SESSION.md").read_text()

    def test_the_request_edits_commands_and_reply_are_in_the_note(self):
        self.send("UserPromptSubmit", prompt="Make the wizard remember its step")
        self.edit("multitool/app.py")
        self.send("PostToolUse", tool_name="Bash", tool_input={"command": "python -m unittest", "description": "Run the tests"})
        self.send("Stop", last_assistant_message="Done: the wizard reopens on your step.")
        note = self.note()
        for text in ("finished the last request", "Make the wizard remember its step", "multitool/app.py",
                     "Run the tests", "> Done: the wizard reopens on your step."):
            self.assertIn(text, note)
        self.assertEqual(self.alerts, [])

    def test_running_out_of_usage_saves_what_claude_was_doing_and_alerts(self):
        transcript = self.root / "transcript.jsonl"
        transcript.write_text("\n".join(json.dumps(line) for line in (
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Next: update the tests."}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}]}},
            {"type": "assistant", "isApiErrorMessage": True,
             "message": {"content": [{"type": "text", "text": "API Error: limit"}]}})))
        self.send("UserPromptSubmit", prompt="Add a Pro Tools filter")
        self.edit("multitool/app.py")
        self.send("StopFailure", error="rate_limit", transcript_path=str(transcript),
                  last_assistant_message="You've hit your limit · resets 3:45pm")
        note = self.note()
        for text in ("stopped before finishing", "hit its usage limit", "> Next: update the tests.", "multitool/app.py"):
            self.assertIn(text, note)
        self.assertEqual(self.alerts, ["Claude hit its usage limit (resets 3:45pm)"])

    def test_other_errors_are_noted_without_an_alert(self):
        self.send("StopFailure", error="overloaded", transcript_path="/nonexistent.jsonl")
        self.assertIn("API error: overloaded", self.note())
        self.assertEqual(self.alerts, [])

    def test_claude_waits_at_the_reset_when_codex_worked_meanwhile(self):
        self.send("UserPromptSubmit", prompt="Add a Pro Tools filter")
        self.send("StopFailure", error="rate_limit", last_assistant_message="You've hit your limit")
        self.touch("notes/HANDOFF.md", self.now + 60)  # Codex takes over
        held = json.loads(self.send("UserPromptSubmit", after=7200, prompt=RESUME))
        self.assertEqual(held["decision"], "block")
        self.assertIn("notes/HANDOFF.md", held["reason"])
        self.assertIn("waiting for you", self.note())
        context = self.send("UserPromptSubmit", prompt="take over")  # the user brings Claude back
        self.assertIn("notes/HANDOFF.md", context)
        self.assertNotIn('"decision"', context)
        self.assertEqual(self.send("UserPromptSubmit", prompt="thanks"), "")  # told once

    def test_claude_continues_at_the_reset_when_nothing_changed(self):
        self.send("UserPromptSubmit", prompt="Add a Pro Tools filter")
        self.edit("multitool/app.py")
        self.send("StopFailure", error="rate_limit")
        self.assertEqual(self.send("UserPromptSubmit", after=7200, prompt=RESUME), "")
        note = self.note()
        self.assertIn("continued by itself", note)
        self.assertNotIn("Your claude.ai usage", note)  # not listed as a request

    def test_claudes_own_edits_never_count_as_someone_elses(self):
        self.send("UserPromptSubmit", prompt="Fix the scroll speed")
        self.edit("multitool/app.py")
        self.edit("notes/HANDOFF.md")
        self.send("Stop", last_assistant_message="Fixed.")
        self.assertEqual(self.send("UserPromptSubmit", after=600, prompt="next thing"), "")

    def test_writing_the_handoff_clears_the_changed_files(self):
        self.send("UserPromptSubmit", prompt="Fix the scroll speed")
        self.edit("multitool/app.py")
        self.edit("notes/HANDOFF.md")
        self.edit("tests/test_app.py")
        files = self.note().split("## Files Claude changed")[1].split("##")[0]
        self.assertIn("tests/test_app.py", files)
        self.assertNotIn("multitool/app.py", files)
        self.assertNotIn("HANDOFF", files)

    def test_keys_are_hidden_and_files_outside_the_project_ignored(self):
        self.send("UserPromptSubmit", prompt="the secret is GOCSPX-abcdefghijklmnop1234 ok")
        self.send("PostToolUse", tool_name="Write", tool_input={"file_path": "/tmp/elsewhere.py"})
        note = self.note()
        self.assertNotIn("GOCSPX", note)
        self.assertIn("the secret is [hidden] ok", note)
        self.assertNotIn("elsewhere.py", note)

    def test_a_broken_state_file_starts_over(self):
        (self.root / ".claude").mkdir()
        (self.root / ".claude/live_session.json").write_text("{not json")
        self.send("UserPromptSubmit", prompt="hello")
        self.assertIn("hello", self.note())

    @unittest.skipUnless((ROOT / ".claude/settings.json").exists(), "local Claude hooks are not shared in the repository")
    def test_claude_code_runs_the_script_on_every_event(self):
        hooks = json.loads((ROOT / ".claude/settings.json").read_text())["hooks"]
        for event in live.HANDLERS:
            hook = hooks[event][0]["hooks"][0]
            self.assertEqual((hook["command"], hook["args"]), ("${CLAUDE_PROJECT_DIR}/.venv/bin/python",
                                                               ["${CLAUDE_PROJECT_DIR}/scripts/live_session.py"]))
        self.assertEqual(set(hooks["PostToolUse"][0]["matcher"].split("|")), {"Edit", "Write", "NotebookEdit", "Bash"})


if __name__ == "__main__":
    unittest.main()
