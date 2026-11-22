"""Run actual failure-alert shell blocks with offline command boundaries."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


WORKFLOWS = ("alert-drill.yml", "scrape.yml")


def _bash():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
    else:
        executable = shutil.which("bash")
        if executable:
            return executable
    pytest.skip("Bash is required for the workflow alert check")


def _alert_script(workflow):
    path = Path(__file__).resolve().parents[1] / ".github/workflows" / workflow
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("- name: Alert on failure"))
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |") + 1
    end = start
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith("          ")):
        end += 1
    script = textwrap.dedent("\n".join(lines[start:end]))
    # Baseline revisions use shared /tmp files; keep those writes inside this fixture.
    return script.replace("/tmp/gh_api_err", '"$TEST_GH_API_ERR"').replace(
        "/tmp/gh_run_err", '"$TEST_GH_RUN_ERR"'
    )


def _run_alert(tmp_path, workflow, *, body='{"ok": true}', http_status=200,
               transport_status=0, missing=None):
    prelude = """
gh() {
    echo "private-token private-chat private response" >&2
    case "$1 $2" in
        "api "*) echo 2026-11-23T00:00:00Z ;;
        "run view") echo "Synthetic failing step" ;;
        "run list") echo 1 ;;
        *) return 99 ;;
    esac
}
curl() {
    printf '%s\\n' "$@" >> "$TEST_CURL_ARGS"
    echo "private-token private-chat private response" >&2
    for arg in "$@"; do
        if [ "$arg" = --fail ] && [ "$TEST_HTTP_STATUS" -ge 400 ]; then return 22; fi
    done
    printf '%s' "$TEST_RESPONSE"
    return "$TEST_TRANSPORT_STATUS"
}
python3() { "$TEST_PYTHON" -B "$@"; }
"""
    env = {key: value for key, value in os.environ.items() if key.upper() in (
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT"
    )}
    env.update({
        "TEST_PYTHON": sys.executable,
        "TEST_HTTP_STATUS": str(http_status),
        "TEST_TRANSPORT_STATUS": str(transport_status),
        "TEST_RESPONSE": body,
        "TEST_CURL_ARGS": str(tmp_path / "curl-args.txt"),
        "TEST_GH_API_ERR": str(tmp_path / "gh-api-err.txt"),
        "TEST_GH_RUN_ERR": str(tmp_path / "gh-run-err.txt"),
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
    if missing:
        env.pop(missing)
    return subprocess.run(
        [_bash(), "--noprofile", "--norc", "-c", prelude + _alert_script(workflow)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )


@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("http_status,body,transport_status,confirmed", [
    (200, '{"ok": true}', 0, True),
    (200, '{"ok": false, "description": "private response"}', 0, False),
    (200, '{}', 0, False),
    (200, 'not json', 0, False),
    (200, '[]', 0, False),
    (200, 'null', 0, False),
    (200, '{"ok": 1}', 0, False),
    (200, '{"ok": "true"}', 0, False),
    (401, '{"ok": true}', 0, False),
    (500, '{"ok": true}', 0, False),
    (200, '{"ok": true}', 28, False),
])
def test_failure_alert_requires_http_and_explicit_ack(
        tmp_path, workflow, http_status, body, transport_status, confirmed):
    result = _run_alert(tmp_path, workflow, body=body, http_status=http_status,
                        transport_status=transport_status)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if confirmed else 1), output
    assert ("failure alert sent" in output) is confirmed
    for sensitive in ("private-token", "private-chat", "private response"):
        assert sensitive not in output
    args = (tmp_path / "curl-args.txt").read_text(encoding="utf-8").splitlines()
    assert args.count("https://api.telegram.org/botprivate-token/sendMessage") == 1
    assert "chat_id=private-chat" in args
    assert "parse_mode=HTML" in args
    text = next(arg for arg in args if arg.startswith("text="))
    title = "i2i ALERT DRILL FAILED" if workflow == "alert-drill.yml" else "i2i SCRAPER run FAILED"
    assert f"<b>{title}</b>" in text
    assert "2026-11-23T00:00:00Z" in text and "Synthetic failing step" in text
    assert "https://github.com/synthetic/fixture/actions/runs/1" in text


@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("missing", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
def test_failure_alert_missing_credentials_fails_without_send(tmp_path, workflow, missing):
    result = _run_alert(tmp_path, workflow, missing=missing)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "failure alert sent" not in result.stdout
    assert not (tmp_path / "curl-args.txt").exists()
