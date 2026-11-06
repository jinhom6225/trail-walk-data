"""Repository settings the collector relies on (security review)."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
COLLECT = (WORKFLOWS / "collect.yml").read_text(encoding="utf-8")
KEEPALIVE = (WORKFLOWS / "keepalive.yml").read_text(encoding="utf-8")


def step(workflow, name):
    match = re.search(r"- name: " + re.escape(name) + r"\n(.*?)(?=\n      - name: |\n  \S|\Z)", workflow, re.S)
    assert match, name
    return match.group(1)


class GitignoreTest(unittest.TestCase):
    def test_caches_keys_and_raw_gpx_are_ignored(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for pattern in ("__pycache__/", "*.pyc", "*.key", ".env", "raw/", "*.gpx", "!collector/tests/fixtures/**/*.gpx"):
            self.assertIn(pattern, lines)


class CollectWorkflowTest(unittest.TestCase):
    def test_daily_0400_kst_and_manual(self):
        self.assertEqual(re.findall(r'(?m)^    - cron: "([^"]+)"', COLLECT), ["0 19 * * *"])
        self.assertIn("workflow_dispatch:", COLLECT)

    def test_least_privilege_permissions(self):
        self.assertRegex(COLLECT, r"(?m)^permissions: \{\}$")
        self.assertRegex(COLLECT, r"(?m)^    permissions:\n      contents: write$")
        self.assertIn("persist-credentials: false", step(COLLECT, "Check out"))

    def test_actions_are_pinned_by_sha(self):
        for uses in re.findall(r"uses: (\S+)", COLLECT):
            self.assertRegex(uses, r"@[0-9a-f]{40}$")

    def test_python_312_and_tests_before_collecting(self):
        self.assertIn('python-version: "3.12"', COLLECT)
        self.assertLess(COLLECT.index("- name: Unit tests"), COLLECT.index("- name: Collect"))

    def test_key_only_in_collect_step_and_masked_first(self):
        collect = step(COLLECT, "Collect")
        self.assertEqual(COLLECT.count("secrets.DATA_GO_KR_SERVICE_KEY"), 1)
        self.assertIn("DATA_GO_KR_SERVICE_KEY: ${{ secrets.DATA_GO_KR_SERVICE_KEY }}", collect)
        self.assertLess(collect.index("collector.mask_key"), collect.index("collector.collect"))
        self.assertNotIn("set -x", COLLECT + KEEPALIVE)

    def test_commit_only_data_and_state_on_main(self):
        commit = step(COLLECT, "Commit changes")
        self.assertIn("if: github.ref == 'refs/heads/main'", commit)
        self.assertIn("git add -A data state", commit)
        self.assertIn("git diff --cached --quiet -- data state", commit)
        self.assertIn('-F "${RUNNER_TEMP}/commit_message.txt"', commit)
        self.assertIn('pull --rebase origin "${GITHUB_REF_NAME}"', commit)
        self.assertEqual(COLLECT.count("github.token"), 1)
        self.assertNotIn('echo "${GH_TOKEN}', commit)

    def test_telegram_secrets_only_in_notice_step(self):
        notice = step(COLLECT, "Telegram notice")
        self.assertIn("if: always()", notice)
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            self.assertEqual(COLLECT.count(f"secrets.{name}"), 1)
            self.assertIn(f"secrets.{name}", notice)
        self.assertIn("Telegram notices are off", notice)
        self.assertIn("exit 0", notice)

    def test_forced_failure_is_manual_only(self):
        self.assertIn("if: inputs.force_fail", step(COLLECT, "Forced failure"))


class KeepaliveWorkflowTest(unittest.TestCase):
    def test_reenables_both_workflows_with_actions_write_only(self):
        self.assertRegex(KEEPALIVE, r"(?m)^permissions: \{\}$")
        self.assertRegex(KEEPALIVE, r"(?m)^    permissions:\n      actions: write\n    steps:$")
        keepalive = step(KEEPALIVE, "Re-enable the workflows")
        self.assertIn("for workflow in collect.yml keepalive.yml", keepalive)
        self.assertIn("actions/workflows/${workflow}/enable", keepalive)
        self.assertIn("::warning::", keepalive)
        self.assertNotIn("secrets.", KEEPALIVE)
        self.assertEqual(len(re.findall(r'(?m)^    - cron: "', KEEPALIVE)), 1)


class TrackedFilesTest(unittest.TestCase):
    def test_no_tracked_raw_gpx_outside_fixtures(self):
        try:
            tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        for name in tracked.splitlines():
            if name.endswith(".gpx"):
                self.assertTrue(name.startswith("collector/tests/fixtures/"), name)

    def test_fixtures_have_no_service_key(self):
        for path in (ROOT / "collector" / "tests" / "fixtures").rglob("*"):
            if path.is_file():
                self.assertNotIn(b"serviceKey=", path.read_bytes(), path.name)


if __name__ == "__main__":
    unittest.main()
