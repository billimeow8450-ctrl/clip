from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence


class CommandError(RuntimeError):
    """Keep the native exit status; a stderr warning is not a crash diagnosis."""

    def __init__(self, message: str, *, command=(), returncode=None,
                 stdout: str = "", stderr: str = "", reason: str = "failed"):
        super().__init__(message)
        self.command = tuple(str(item) for item in command)
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.reason = reason
        self.diagnostic_path = None

    @property
    def native_crash(self) -> bool:
        # Never retry cancellation, timeout, SIGKILL/OOM or ordinary bad input.
        return self.reason == "failed" and self.returncode in {-11, -7, -6, -4}

    @property
    def signal_name(self) -> str:
        try:
            return signal.Signals(-self.returncode).name if self.returncode < 0 else ""
        except (TypeError, ValueError):
            return ""

    def __str__(self) -> str:
        message = super().__str__()
        if self.diagnostic_path:
            message += f"\nDiagnostic report: {self.diagnostic_path}"
        return message


Progress = Optional[Callable[[str, float, str], None]]


def emit(progress: Progress, stage: str, fraction: float, detail: str) -> None:
    if progress:
        progress(stage, max(0.0, min(1.0, float(fraction))), str(detail))


def cancelled(cancel_check: Optional[Callable[[], bool]]) -> bool:
    try:
        return bool(cancel_check and cancel_check())
    except Exception:
        return False


def run(
    command: Sequence[str],
    *,
    timeout: Optional[float] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    cwd: Optional[Path] = None,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(item) for item in command]
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            cwd=str(cwd) if cwd else None,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        raise CommandError(f"Cannot start {command[0]}: {exc}",
                           command=command, reason="launch") from exc
    started = time.monotonic()
    def terminate() -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()

    while True:
        if cancelled(cancel_check):
            terminate()
            raise CommandError("Master Editor job cancelled", command=command, reason="cancelled")
        if timeout and time.monotonic() - started > timeout:
            terminate()
            raise CommandError(
                f"Command timed out after {int(timeout)}s: {shlex.join(command[:8])}",
                command=command, reason="timeout",
            )
        try:
            # Drain both pipes while the child runs, avoiding a full-pipe deadlock.
            stdout, stderr = proc.communicate(timeout=.25)
            break
        except subprocess.TimeoutExpired:
            continue
    if proc.returncode:
        tail = "\n".join((stderr or stdout or "").splitlines()[-35:])
        raise CommandError(
            f"Command failed ({proc.returncode}): {shlex.join(command[:10])}\n{tail}",
            command=command, returncode=proc.returncode, stdout=stdout, stderr=stderr,
        )
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def write_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def filter_escape(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


def atomic_replace(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def chunks(values: Iterable[object], size: int):
    batch = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
