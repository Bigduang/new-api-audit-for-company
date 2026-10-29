import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/scripts/run-daily-audit.sh"
ALL_STEPS = ["classify", "summarize-work", "push-wecom", "cleanup"]


@pytest.mark.parametrize(
    ("failed_step", "cleanup_status", "expected_status", "expected_steps"),
    [
        ("", 0, 0, ALL_STEPS),
        ("classify", 0, 23, ["classify", "cleanup"]),
        ("summarize-work", 0, 23, ["classify", "summarize-work", "cleanup"]),
        ("push-wecom", 0, 23, ALL_STEPS),
        ("", 29, 29, ALL_STEPS),
        ("push-wecom", 29, 23, ALL_STEPS),
        ("classify", 29, 23, ["classify", "cleanup"]),
    ],
)
def test_daily_audit_always_cleans_up(
    tmp_path, monkeypatch, failed_step, cleanup_status, expected_status, expected_steps
):
    calls = tmp_path / "calls.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$AUDIT_TEST_CALLS"\n'
        'if [ "$6" = "$AUDIT_TEST_FAILED_STEP" ]; then exit 23; fi\n'
        'if [ "$6" = cleanup ]; then exit "$AUDIT_TEST_CLEANUP_STATUS"; fi\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AUDIT_TEST_CALLS", str(calls))
    monkeypatch.setenv("AUDIT_TEST_FAILED_STEP", failed_step)
    monkeypatch.setenv("AUDIT_TEST_CLEANUP_STATUS", str(cleanup_status))

    result = subprocess.run(
        ["sh", str(SCRIPT), "2026-10-28"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == expected_status, result.stderr
    assert [line.split() for line in calls.read_text().splitlines()] == [
        ["exec", "token-audit", "python", "-m", "token_audit.cli", step]
        + ([] if step == "cleanup" else ["--start", "2026-10-28", "--end", "2026-10-28"])
        for step in expected_steps
    ]
