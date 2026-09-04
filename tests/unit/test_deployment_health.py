"""Behavioral tests for the bounded deployment readiness gate."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "patt-wait-for-health.sh"
DEPLOYMENT_SHA = "a" * 40


def _bash_path(path: Path) -> str:
    value = path.as_posix()
    if value.lower().startswith("//wsl.localhost/"):
        parts = value.split("/", 4)
        if len(parts) == 5:
            return f"/{parts[4]}"
    return value


def _run_health_gate(
    tmp_path: Path, *, ready_after: int
) -> subprocess.CompletedProcess:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    count_file = tmp_path / "attempts"
    curl = fake_bin / "curl"
    curl.write_bytes(
        b"""#!/bin/sh
count=0
if [ -s "$PATT_HEALTH_COUNT_FILE" ]; then count=$(cat "$PATT_HEALTH_COUNT_FILE"); fi
count=$((count + 1))
printf '%s\n' "$count" > "$PATT_HEALTH_COUNT_FILE"
if [ "$count" -lt "$PATT_HEALTH_READY_AFTER" ]; then exit 22; fi
printf '%s' '{"ok":true,"data":{"db":"connected","environment":"production","version":"0.24.3","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}'
"""
    )
    sleep = fake_bin / "sleep"
    sleep.write_bytes(b"#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    sleep.chmod(0o755)

    shell_command = (
        f"chmod 700 {shlex.quote(_bash_path(curl))} {shlex.quote(_bash_path(sleep))}; "
        f'export PATH={shlex.quote(_bash_path(fake_bin))}:"$PATH"; '
        f"export PATT_HEALTH_COUNT_FILE={shlex.quote(_bash_path(count_file))}; "
        f"export PATT_HEALTH_READY_AFTER={ready_after}; "
        f"exec bash {shlex.quote(_bash_path(SCRIPT))} production 0.24.3 {DEPLOYMENT_SHA}"
    )
    bash = shutil.which("bash") or "bash"
    command = [bash, "-c", shell_command]
    if SCRIPT.as_posix().lower().startswith("//wsl.localhost/"):
        wsl = shutil.which("wsl")
        assert wsl is not None
        command = [wsl, "--", "bash", "-c", shell_command]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def test_readiness_can_succeed_after_the_former_thirty_second_window(tmp_path):
    result = _run_health_gate(tmp_path, ready_after=11)

    assert result.returncode == 0
    assert "15s elapsed" in result.stderr
    assert "30s elapsed" in result.stderr
    assert "45s elapsed" not in result.stderr


def test_readiness_fails_closed_at_the_ninety_second_deadline(tmp_path):
    result = _run_health_gate(tmp_path, ready_after=31)

    assert result.returncode == 1
    assert "90s elapsed" in result.stderr
    assert "Deployment readiness failed after 90s" in result.stderr
