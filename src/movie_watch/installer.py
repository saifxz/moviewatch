from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

DEFAULT_TASK = "MovieTicketWatch"


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(args: list[str], *, check: bool = True) -> str:
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if check and completed.returncode != 0:
        raise RuntimeError(output.strip() or f"command exited {completed.returncode}")
    return output.strip()


def _companion_names(task_name: str) -> list[str]:
    return [task_name] + [f"{task_name}-{i}" for i in range(2, 11)]


def _install_minute_task(
    command: str, seconds: int, offset_seconds: int, task_name: str, *, python: str
) -> str:
    """Create a 1-minute repeating task with a custom start offset.

    Task Scheduler rejects intervals below 60s, so several offset tasks
    together produce a sub-minute effective polling interval.
    """
    quoted_python = _quote(python)
    arg_string = command[len(quoted_python) :] if command.startswith(quoted_python) else command
    script = f"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute {_ps_quote(python)} -Argument {_ps_quote(arg_string)}
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds({offset_seconds}) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName {_ps_quote(task_name)} -Action $action `
    -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
Write-Output ('SUCCESS: ' + {_ps_quote(task_name)})
"""
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def install_task(config_path: Path, interval_seconds: int, task_name: str = DEFAULT_TASK) -> str:
    python = sys.executable
    command = (
        f"{_quote(python)} -m movie_watch run --once "
        f"--config {_quote(str(config_path))}"
    )
    seconds = max(1, int(interval_seconds))
    outputs: list[str] = []

    if seconds >= 60:
        outputs.append(
            _run(
                [
                    "schtasks",
                    "/Create",
                    "/TN",
                    task_name,
                    "/TR",
                    command,
                    "/SC",
                    "MINUTE",
                    "/MO",
                    str(seconds // 60),
                    "/RL",
                    "LIMITED",
                    "/F",
                ]
            )
        )
        names = [task_name]
    else:
        count = math.ceil(60 / seconds)
        names = [task_name] + [f"{task_name}-{i}" for i in range(2, count + 1)]
        for index, name in enumerate(names):
            outputs.append(
                _install_minute_task(
                    command, seconds, index * seconds, name, python=python
                )
            )

    # Remove companion tasks left over from a previous, faster configuration.
    for stale in _companion_names(task_name):
        if stale not in names:
            _run(["schtasks", "/Delete", "/TN", stale, "/F"], check=False)

    return "\n".join(outputs)


def uninstall_task(task_name: str = DEFAULT_TASK) -> str:
    outputs = []
    for name in _companion_names(task_name):
        result = _run(["schtasks", "/Delete", "/TN", name, "/F"], check=False)
        if result:
            outputs.append(result)
    return "\n".join(outputs) or f"no task named {task_name} found"


def query_task(task_name: str = DEFAULT_TASK) -> str:
    outputs = []
    for name in _companion_names(task_name):
        result = _run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"], check=False)
        if result and "ERROR" not in result.upper():
            outputs.append(result)
    if not outputs:
        raise RuntimeError(f"task not found: {task_name}")
    return "\n\n".join(outputs)
