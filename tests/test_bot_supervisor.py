"""Execute the workflow supervisor with harmless local command substitutes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


def _bash():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
        pytest.skip("Git Bash is required for the workflow supervisor check")
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("Bash is required for the workflow supervisor check")
    return executable


def _workflow_script(step_name):
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/telegram-bot.yml"
    lines = workflow.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(
        f"- name: {step_name}"
    ))
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |") + 1
    end = start
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith("          ")):
        end += 1
    return textwrap.dedent("\n".join(lines[start:end]))


@pytest.mark.parametrize("child_status", [0, 7])
def test_supervisor_propagates_child_status(tmp_path, child_status):
    result = _run_supervisor(tmp_path, child_status=child_status, handoff=False)
    assert result.returncode == child_status, result.stdout + result.stderr
    assert "FAKE_DISPATCH" not in result.stdout


def test_supervisor_intentional_handoff_is_success(tmp_path):
    result = _run_supervisor(tmp_path, child_status=0, handoff=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("FAKE_DISPATCH") == 1
    assert "handoff complete" in result.stdout


def _run_supervisor(tmp_path, *, child_status, handoff):
    # Substitute every external side-effecting command; the child is only a
    # shell return or an exec'd sleep that the real supervisor kills and reaps.
    prelude = """
git() { return 0; }
gh() { echo FAKE_DISPATCH; }
python3() {
    if [ "$TEST_HANDOFF" = 1 ]; then exec /usr/bin/sleep 5; fi
    return "$TEST_CHILD_STATUS"
}
sleep() { /usr/bin/sleep 0.01; }
date() { echo 0; }
trap 'for child in $(jobs -p); do kill "$child" 2>/dev/null || true; done; wait 2>/dev/null || true' EXIT
"""
    env = {key: value for key, value in os.environ.items() if key.upper() in (
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP"
    )}
    env.update({
        "TEST_CHILD_STATUS": str(child_status),
        "TEST_HANDOFF": "1" if handoff else "0",
        "BOT_HANDOFF_MIN": "0" if handoff else "340",
        "BOT_STATE_PUSH_S": "60",
        "GITHUB_REPOSITORY": "synthetic/fixture",
        "HOME": str(tmp_path),
    })
    return subprocess.run(
        [_bash(), "--noprofile", "--norc", "-c", prelude + _workflow_script("Run Telegram command bot")],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )


@pytest.mark.parametrize("http_status,body,confirmed", [
    (200, '{"ok": true}', True),
    (200, '{"ok": false, "description": "private response"}', False),
    (200, '{}', False),
    (200, '{"ok": 1}', False),
    (200, 'not json', False),
    (401, '{"ok": true}', False),
])
def test_failure_alert_requires_http_and_api_success(tmp_path, http_status, body, confirmed):
    prelude = """
gh() { echo unknown; }
curl() {
    echo "private-token private-chat private response" >&2
    for arg in "$@"; do
        if [ "$arg" = --fail ] && [ "$TEST_HTTP_STATUS" -ge 400 ]; then return 22; fi
    done
    printf '%s' "$TEST_RESPONSE"
}
python3() { "$TEST_PYTHON" "$@"; }
"""
    env = {key: value for key, value in os.environ.items() if key.upper() in (
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"
    )}
    env.update({
        "TEST_PYTHON": sys.executable,
        "TEST_HTTP_STATUS": str(http_status),
        "TEST_RESPONSE": body,
        "TELEGRAM_BOT_TOKEN": "private-token",
        "TELEGRAM_CHAT_ID": "private-chat",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "synthetic/fixture",
        "GITHUB_RUN_ID": "1",
        "GITHUB_WORKFLOW": "synthetic",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    })
    # Old workflow revisions use fixed /tmp files for metadata stderr. Keep
    # this fixture local without changing the delivery code under test.
    script = _workflow_script("Alert on failure").replace("/tmp/gh_api_err", "/dev/null").replace("/tmp/gh_run_err", "/dev/null")
    result = subprocess.run(
        [_bash(), "--noprofile", "--norc", "-c", prelude + script],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert (result.returncode == 0) is confirmed, result.stdout + result.stderr
    assert ("failure alert sent" in result.stdout) is confirmed
    for sensitive in ("private-token", "private-chat", "private response"):
        assert sensitive not in result.stdout + result.stderr
