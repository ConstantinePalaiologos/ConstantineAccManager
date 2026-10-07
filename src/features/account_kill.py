"""
features/account_kill.py
Close the Roblox window that belongs to a saved account, and nothing else.
"""

from __future__ import annotations

import subprocess
import time

import psutil

import features.auto_rejoin as auto_rejoin
import features.presence as presence_mod
from classes.operation_result import OperationResult

_ATTEMPTS = 3
_WAIT_SECONDS = 3.0


def _still_running(identity: tuple[int, float]) -> bool:
    pid, created = identity
    current = presence_mod.get_roblox_processes(force=True).get(pid)
    return bool(current and abs(current[0] - created) <= 0.01)


def _close(identity: tuple[int, float]) -> tuple[bool, str]:
    # The process is matched on PID and start time so a reused PID is never hit.
    pid, created = identity
    detail = ""
    for _ in range(_ATTEMPTS):
        if not _still_running(identity):
            return True, ""
        try:
            result = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()[-300:]
                try:
                    process = psutil.Process(pid)
                    if abs(process.create_time() - created) <= 0.01:
                        process.kill()
                except (OSError, psutil.Error):
                    pass
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
        deadline = time.monotonic() + _WAIT_SECONDS
        while time.monotonic() < deadline:
            if not _still_running(identity):
                return True, ""
            time.sleep(0.25)
    return False, detail or "The process is still running."


def kill_accounts(manager, usernames: list[str]) -> OperationResult:
    user_ids: dict[str, str] = {}
    missing: list[str] = []
    no_user_id: list[str] = []
    lock = getattr(manager, "_accounts_lock", None)
    if lock is not None:
        with lock:
            records = {name: manager.accounts.get(name) for name in usernames}
    else:
        records = {name: manager.accounts.get(name) for name in usernames}
    for name, record in records.items():
        if not isinstance(record, dict):
            missing.append(name)
            continue
        user_id = str(record.get("user_id", "") or "")
        if not user_id or user_id == "0":
            no_user_id.append(name)
        else:
            user_ids[name] = user_id

    found = auto_rejoin.scan_pid_uid_map(set(user_ids.values())) if user_ids else {}

    closed: dict[str, int] = {}
    not_running: list[str] = []
    failed: dict[str, list[str]] = {}
    for name, user_id in user_ids.items():
        identities = sorted(found.get(user_id, set()), key=lambda item: item[1])
        if not identities:
            not_running.append(name)
            continue
        count = 0
        for identity in identities:
            ok, detail = _close(identity)
            if ok:
                count += 1
                print(f"[SUCCESS] Closed Roblox PID {identity[0]} for {name}.")
            else:
                failed.setdefault(name, []).append(f"PID {identity[0]}: {detail}")
                print(f"[ERROR] Could not close Roblox PID {identity[0]} for {name}: {detail}")
        if count:
            closed[name] = count

    data = {
        "closed": closed,
        "not_running": not_running,
        "failed": failed,
        "no_user_id": no_user_id,
        "missing": missing,
    }
    problems = []
    if failed:
        problems.append("Could not close the Roblox window of: " + ", ".join(failed) + ".")
    if no_user_id:
        problems.append(
            "No saved user ID for: " + ", ".join(no_user_id)
            + ", so their window cannot be identified. Launch the account once from this app to save it."
        )
    if missing:
        problems.append("No longer in the account list: " + ", ".join(missing) + ".")
    if problems:
        return OperationResult(
            False,
            code="ROBLOX_ACCOUNT_KILL_FAILED",
            title="Roblox Window Could Not Be Closed",
            message="\n\n".join(problems),
            detail="\n".join(line for lines in failed.values() for line in lines),
            data=data,
        )
    total = sum(closed.values())
    if total:
        message = f"Closed {total} Roblox window{'s' if total != 1 else ''}."
    else:
        message = "No running Roblox window was found."
    return OperationResult.success(message, data=data)
