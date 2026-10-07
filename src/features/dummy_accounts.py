"""
features/dummy_accounts.py
Per-account "dummy" profile: low FPS and graphics at launch, plus working-set
trimming that only touches Roblox processes belonging to dummy accounts.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time

import psutil

import features.presence as presence_mod
import features.roblox_settings as roblox_settings_mod
import features.settings_store as settings_store_mod
from classes.operation_result import OperationResult

DEFAULTS = {
    "dummy_framerate_cap": 5,
    "dummy_quality_level": 1,
    "dummy_ram_limit_mb": 300,
    "dummy_ram_trim_delay_sec": 90,
    "dummy_min_free_ram_mb": 2500,
}
_LIMITS = {
    "dummy_framerate_cap": (1, 999),
    "dummy_quality_level": (1, 10),
    "dummy_ram_limit_mb": (50, 8192),
    "dummy_ram_trim_delay_sec": (0, 900),
    "dummy_min_free_ram_mb": (0, 16000),
}
RAM_GUARD_TIMEOUT_SECONDS = 180.0
RAM_GUARD_POLL_SECONDS = 3.0
_RAM_GUARD_LOG_SECONDS = 15.0
_SNAPSHOT_KEY = "dummy_restore_snapshot"
_MANAGED_KEYS = ("FramerateCap", "SavedQualityLevel")
# Roblox reads its settings file shortly after the process starts, so a launch
# that needs different values waits this long after the previous write.
SETTLE_SECONDS = 10.0
TRIM_INTERVAL_SECONDS = 15
_LOG_INTERVAL_SECONDS = 60
HARD_LIMIT_KEY = "dummy_ram_hard_limit"

_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_INFORMATION = 0x0400
_QUOTA_LIMITS_HARDWS_MAX_ENABLE = 0x00000004
_QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000008
_QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000010
_MIN_WORKING_SET_BYTES = 204800

_launch_lock = threading.Lock()
_last_profile: str | None = None
_last_apply_time = 0.0


def get_dummy_settings() -> dict[str, int]:
    values: dict[str, int] = {}
    for key, default in DEFAULTS.items():
        low, high = _LIMITS[key]
        try:
            value = int(settings_store_mod.get(key, default))
        except (TypeError, ValueError):
            value = default
        values[key] = max(low, min(high, value))
    return values


def hard_limit_enabled() -> bool:
    return bool(settings_store_mod.get(HARD_LIMIT_KEY, False))


def is_dummy_account(manager, username: str | None) -> bool:
    if not username:
        return False
    lock = getattr(manager, "_accounts_lock", None)
    if lock is not None:
        with lock:
            data = manager.accounts.get(username)
    else:
        data = manager.accounts.get(username)
    return isinstance(data, dict) and bool(data.get("dummy", False))


def _current_managed_values() -> OperationResult:
    loaded = roblox_settings_mod.load_settings()
    if not loaded:
        return loaded
    records = {
        str(record["key"]): str(record.get("value", ""))
        for record in (loaded.data or {}).get("settings", [])
    }
    return OperationResult.success(
        data={key: records[key] for key in _MANAGED_KEYS if key in records}
    )


def _write_values(values: dict[str, str]) -> OperationResult:
    if not values:
        return OperationResult.success()
    return roblox_settings_mod.apply_settings(
        values,
        expected_hash="",
        framerate_locked=None,
    )


def restore_normal_profile() -> OperationResult:
    snapshot = settings_store_mod.get(_SNAPSHOT_KEY)
    if not isinstance(snapshot, dict) or not snapshot:
        return OperationResult.success()
    values = {
        key: str(snapshot[key])
        for key in _MANAGED_KEYS
        if key in snapshot
    }
    result = _write_values(values)
    if result:
        settings_store_mod.remove(_SNAPSHOT_KEY)
    return result


def _apply_dummy_profile() -> OperationResult:
    base = roblox_settings_mod.apply_saved_customizations()
    if base is not None and not base:
        return base

    current = _current_managed_values()
    if not current:
        return current
    if not isinstance(settings_store_mod.get(_SNAPSHOT_KEY), dict):
        settings_store_mod.save(_SNAPSHOT_KEY, dict(current.data or {}))

    cfg = get_dummy_settings()
    target = {
        "FramerateCap": str(cfg["dummy_framerate_cap"]),
        "SavedQualityLevel": str(cfg["dummy_quality_level"]),
    }
    changes = {
        key: value
        for key, value in target.items()
        if (current.data or {}).get(key) != value
    }
    return _write_values(changes)


def _apply_normal_profile() -> OperationResult:
    restored = restore_normal_profile()
    if not restored:
        return restored
    return roblox_settings_mod.apply_saved_customizations()


def _available_ram_mb() -> float:
    return psutil.virtual_memory().available / 1024 / 1024


def wait_for_free_ram(username: str | None = None) -> OperationResult | None:
    """Hold a dummy launch until enough RAM is free, so a burst of windows
    loading in at once cannot exhaust memory."""
    minimum = get_dummy_settings()["dummy_min_free_ram_mb"]
    if minimum <= 0:
        return None
    deadline = time.monotonic() + RAM_GUARD_TIMEOUT_SECONDS
    last_log = 0.0
    while True:
        available = _available_ram_mb()
        if available >= minimum:
            return None
        now = time.monotonic()
        if now >= deadline:
            return OperationResult.failure(
                "LOW_FREE_RAM",
                "Not Enough Free RAM",
                f"{username or 'This account'} was not launched because only "
                f"{available:.0f} MB of RAM was free for "
                f"{RAM_GUARD_TIMEOUT_SECONDS:.0f} seconds (needs {minimum} MB). "
                "Close a window, lower the RAM limit, or turn this check down "
                "under Settings > Dummy.",
            )
        if now - last_log >= _RAM_GUARD_LOG_SECONDS:
            last_log = now
            print(
                f"[INFO] Holding {username or 'launch'}: {available:.0f} MB free, "
                f"waiting for {minimum} MB."
            )
        time.sleep(RAM_GUARD_POLL_SECONDS)


def apply_launch_profile(manager, username: str | None = None) -> OperationResult | None:
    global _last_profile, _last_apply_time
    profile = "dummy" if is_dummy_account(manager, username) else "normal"
    if profile == "dummy":
        held = wait_for_free_ram(username)
        if held is not None:
            return held
    with _launch_lock:
        if _last_profile is not None and _last_profile != profile:
            remaining = SETTLE_SECONDS - (time.monotonic() - _last_apply_time)
            if remaining > 0:
                print(
                    f"[INFO] Waiting {remaining:.0f}s so the previous Roblox "
                    f"window can read its settings before switching to the "
                    f"{profile} profile."
                )
                time.sleep(remaining)
        if profile == "dummy":
            result = _apply_dummy_profile()
        else:
            result = _apply_normal_profile()
        if result is None or result:
            _last_profile = profile
            _last_apply_time = time.monotonic()
        return result


def _open_quota_handle(kernel32, pid: int):
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_QUERY_INFORMATION,
        False,
        pid,
    )


def _empty_working_set(pid: int) -> tuple[bool, str]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    psapi.EmptyWorkingSet.restype = wintypes.BOOL

    handle = _open_quota_handle(kernel32, pid)
    if not handle:
        return False, f"OpenProcess failed (error {ctypes.get_last_error()})"
    try:
        if not psapi.EmptyWorkingSet(handle):
            return False, f"EmptyWorkingSet failed (error {ctypes.get_last_error()})"
        return True, ""
    finally:
        kernel32.CloseHandle(handle)


def _set_working_set_limits(pid: int, maximum_bytes: int, flags: int) -> tuple[bool, str]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetProcessWorkingSetSizeEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.SetProcessWorkingSetSizeEx.restype = wintypes.BOOL

    handle = _open_quota_handle(kernel32, pid)
    if not handle:
        return False, f"OpenProcess failed (error {ctypes.get_last_error()})"
    try:
        if not kernel32.SetProcessWorkingSetSizeEx(
            handle,
            _MIN_WORKING_SET_BYTES,
            int(maximum_bytes),
            flags,
        ):
            return False, f"SetProcessWorkingSetSizeEx failed (error {ctypes.get_last_error()})"
        return True, ""
    finally:
        kernel32.CloseHandle(handle)


def _apply_hard_working_set(pid: int, limit_mb: int) -> tuple[bool, str]:
    return _set_working_set_limits(
        pid,
        limit_mb * 1024 * 1024,
        _QUOTA_LIMITS_HARDWS_MAX_ENABLE | _QUOTA_LIMITS_HARDWS_MIN_DISABLE,
    )


def _release_hard_working_set(pid: int) -> tuple[bool, str]:
    release_max = min(psutil.virtual_memory().total // 2, 8 * 1024 ** 3)
    return _set_working_set_limits(
        pid,
        release_max,
        _QUOTA_LIMITS_HARDWS_MAX_DISABLE | _QUOTA_LIMITS_HARDWS_MIN_DISABLE,
    )


class DummyRamTrimmer:
    def __init__(self, manager):
        self._manager = manager
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid_uid: dict[tuple[int, float], str] = {}
        self._last_log: dict[int, float] = {}
        self._failed_pids: set[int] = set()
        self._hard_capped: set[tuple[int, float]] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="DummyRamTrimmer",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=1.5)

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                print(f"[ERROR] Dummy RAM trim scan failed: {type(exc).__name__}: {exc}")
            if self._stop_evt.wait(TRIM_INTERVAL_SECONDS):
                break

    def _dummy_user_ids(self) -> set[str]:
        uid_map = presence_mod._build_uid_map(self._manager)
        lock = getattr(self._manager, "_accounts_lock", None)
        if lock is not None:
            with lock:
                dummies = {
                    name
                    for name, data in self._manager.accounts.items()
                    if isinstance(data, dict) and data.get("dummy")
                }
        else:
            dummies = {
                name
                for name, data in self._manager.accounts.items()
                if isinstance(data, dict) and data.get("dummy")
            }
        return {uid for uid, name in uid_map.items() if name in dummies}

    def scan_once(self) -> None:
        dummy_uids = self._dummy_user_ids()
        if not dummy_uids:
            return

        cfg = get_dummy_settings()
        limit_mb = cfg["dummy_ram_limit_mb"]
        delay = cfg["dummy_ram_trim_delay_sec"]
        hard = hard_limit_enabled()
        processes = presence_mod.get_roblox_processes()
        live = {(pid, value[0]) for pid, value in processes.items()}
        self._pid_uid = {k: v for k, v in self._pid_uid.items() if k in live}
        self._failed_pids &= {pid for pid, _ in live}
        self._hard_capped &= live

        used_logs: set[str] = set()
        now = time.time()
        for pid in sorted(processes):
            create_time, process = processes[pid]
            if now - create_time < delay:
                continue
            key = (pid, create_time)
            uid = self._pid_uid.get(key)
            if uid is None:
                uid = presence_mod._get_user_id_from_pid(pid, used_logs)
                if uid:
                    self._pid_uid[key] = uid
            if uid not in dummy_uids:
                if key in self._hard_capped:
                    _release_hard_working_set(pid)
                    self._hard_capped.discard(key)
                continue

            if hard:
                if key not in self._hard_capped:
                    ok, error = _apply_hard_working_set(pid, limit_mb)
                    if ok:
                        self._hard_capped.add(key)
                        print(f"[INFO] Dummy hard limit: PID {pid} capped at {limit_mb} MB")
                    elif pid not in self._failed_pids:
                        self._failed_pids.add(pid)
                        print(f"[WARNING] Dummy hard limit could not be set on PID {pid}: {error}")
                if key in self._hard_capped:
                    continue
            elif key in self._hard_capped:
                _release_hard_working_set(pid)
                self._hard_capped.discard(key)

            try:
                rss_mb = process.memory_info().rss / 1024 / 1024
            except Exception:
                continue
            if rss_mb < limit_mb:
                continue
            ok, error = _empty_working_set(pid)
            if ok:
                self._log_once(pid, f"[INFO] Dummy trim: PID {pid} was {rss_mb:.0f} MB before trim")
            elif pid not in self._failed_pids:
                self._failed_pids.add(pid)
                print(f"[WARNING] Dummy trim could not touch PID {pid}: {error}")

    def _log_once(self, pid: int, message: str) -> None:
        now = time.monotonic()
        if now - self._last_log.get(pid, 0.0) >= _LOG_INTERVAL_SECONDS:
            self._last_log[pid] = now
            print(message)
