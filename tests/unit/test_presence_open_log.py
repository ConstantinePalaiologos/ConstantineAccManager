import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from features import presence

LOG_NAME = "0.741.0.7411058_20261007T151819Z_Player_8C3CB_last.log"
CRASH_NAME = "0.741.0.7411058_20261007T151820Z_Player_E8B2A_CrashHandler_last.log"


class FakeProcess:
    def __init__(self, paths, create_time=1.0):
        self._paths = paths
        self._create_time = create_time

    def open_files(self):
        return [SimpleNamespace(path=path) for path in self._paths]

    def create_time(self):
        return self._create_time


class OpenLogMatchingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        logs = os.path.join(self.folder.name, "Roblox", "logs")
        os.makedirs(logs)
        self.logs = logs
        patcher = patch.dict(os.environ, {"LOCALAPPDATA": self.folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        presence._LOG_CACHE.clear()

    def write_log(self, name, user_id):
        path = os.path.join(self.logs, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"header userid:{user_id},more\n")
        return path

    def test_window_is_matched_by_the_log_it_has_open_with_no_time_limit(self):
        path = self.write_log(LOG_NAME, "12345")
        proc = FakeProcess([path], create_time=1.0)
        with patch.object(presence.psutil, "Process", return_value=proc):
            self.assertEqual(presence._get_user_id_from_pid(99), "12345")

    def test_open_log_is_recorded_as_used(self):
        path = self.write_log(LOG_NAME, "12345")
        used = set()
        proc = FakeProcess([path])
        self.assertEqual(presence._get_user_id_from_open_log(proc, used), "12345")
        self.assertEqual(used, {os.path.join(self.logs, LOG_NAME)})

    def test_crash_handler_logs_are_ignored(self):
        path = self.write_log(CRASH_NAME, "999")
        self.assertIsNone(presence._get_user_id_from_open_log(FakeProcess([path])))

    def test_unrelated_open_files_are_ignored(self):
        self.assertIsNone(
            presence._get_user_id_from_open_log(FakeProcess(["C:/Windows/notes.txt"]))
        )

    def test_access_denied_falls_back_instead_of_failing(self):
        class Denied:
            def open_files(self):
                raise psutil.AccessDenied(1)

        self.assertIsNone(presence._get_user_id_from_open_log(Denied()))


if __name__ == "__main__":
    unittest.main()
