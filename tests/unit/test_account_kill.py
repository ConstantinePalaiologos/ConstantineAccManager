import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil

from features import account_kill


class FakeManager:
    def __init__(self, accounts):
        self.accounts = accounts
        self._accounts_lock = threading.RLock()


def accounts():
    return {
        "alpha": {"user_id": 111},
        "beta": {"user_id": "222"},
        "no_id": {"user_id": 0},
        "blank": {},
    }


class KillAccountsTests(unittest.TestCase):
    def run_kill(self, names, found, close=lambda identity: (True, "")):
        closed_identities = []

        def fake_close(identity):
            closed_identities.append(identity)
            return close(identity)

        with patch.object(account_kill.auto_rejoin, "scan_pid_uid_map", return_value=found) as scan, \
                patch.object(account_kill, "_close", side_effect=fake_close):
            result = account_kill.kill_accounts(FakeManager(accounts()), names)
        return result, closed_identities, scan

    def test_only_the_requested_accounts_window_is_closed(self):
        found = {"111": {(100, 1.0)}, "222": {(200, 2.0)}}
        result, closed, scan = self.run_kill(["alpha"], found)
        self.assertTrue(result)
        self.assertEqual(closed, [(100, 1.0)])
        self.assertEqual(result.data["closed"], {"alpha": 1})
        self.assertEqual(scan.call_args.args[0], {"111"})

    def test_every_window_of_that_account_is_closed(self):
        found = {"111": {(100, 1.0), (101, 2.0)}}
        result, closed, _ = self.run_kill(["alpha"], found)
        self.assertEqual(sorted(closed), [(100, 1.0), (101, 2.0)])
        self.assertEqual(result.data["closed"], {"alpha": 2})

    def test_several_accounts_are_each_handled(self):
        found = {"111": {(100, 1.0)}, "222": {(200, 2.0)}}
        result, closed, _ = self.run_kill(["alpha", "beta"], found)
        self.assertEqual(sorted(closed), [(100, 1.0), (200, 2.0)])
        self.assertEqual(result.data["closed"], {"alpha": 1, "beta": 1})

    def test_an_account_with_no_running_window_is_reported_not_failed(self):
        result, closed, _ = self.run_kill(["alpha"], {})
        self.assertTrue(result)
        self.assertEqual(closed, [])
        self.assertEqual(result.data["not_running"], ["alpha"])
        self.assertIn("No running Roblox window", result.message)

    def test_an_account_without_a_saved_user_id_cannot_be_matched(self):
        result, closed, scan = self.run_kill(["no_id", "blank"], {})
        self.assertFalse(result)
        self.assertEqual(result.code, "ROBLOX_ACCOUNT_KILL_FAILED")
        self.assertEqual(result.data["no_user_id"], ["no_id", "blank"])
        self.assertEqual(closed, [])
        scan.assert_not_called()

    def test_an_unknown_account_is_reported(self):
        result, closed, _ = self.run_kill(["ghost"], {})
        self.assertFalse(result)
        self.assertEqual(result.data["missing"], ["ghost"])

    def test_a_window_that_will_not_close_is_a_failure_with_details(self):
        found = {"111": {(100, 1.0)}}
        result, _, _ = self.run_kill(["alpha"], found, close=lambda identity: (False, "Access is denied."))
        self.assertFalse(result)
        self.assertIn("alpha", result.message)
        self.assertIn("Access is denied.", result.detail)
        self.assertEqual(result.data["failed"], {"alpha": ["PID 100: Access is denied."]})

    def test_one_failure_does_not_stop_the_other_accounts(self):
        found = {"111": {(100, 1.0)}, "222": {(200, 2.0)}}
        result, closed, _ = self.run_kill(
            ["alpha", "beta"], found,
            close=lambda identity: (identity[0] != 100, "denied"),
        )
        self.assertEqual(sorted(closed), [(100, 1.0), (200, 2.0)])
        self.assertEqual(result.data["closed"], {"beta": 1})
        self.assertIn("alpha", result.data["failed"])


class CloseProcessTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(account_kill.time, "sleep", lambda _s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def running(self, *states):
        values = list(states)
        return patch.object(
            account_kill, "_still_running",
            side_effect=lambda identity: values.pop(0) if len(values) > 1 else values[0],
        )

    def test_a_process_that_is_already_gone_counts_as_closed(self):
        with self.running(False), patch.object(account_kill.subprocess, "run") as run:
            self.assertEqual(account_kill._close((100, 1.0)), (True, ""))
        run.assert_not_called()

    def test_taskkill_is_aimed_at_exactly_that_pid_and_its_children(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with self.running(True, False), patch.object(account_kill.subprocess, "run", return_value=completed) as run:
            self.assertEqual(account_kill._close((4242, 1.0)), (True, ""))
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])

    def test_it_falls_back_to_killing_the_process_directly(self):
        failed = SimpleNamespace(returncode=1, stdout="", stderr="ERROR: denied")
        process = MagicMock()
        process.create_time.return_value = 1.0
        with self.running(True, True, False), \
                patch.object(account_kill.subprocess, "run", return_value=failed), \
                patch.object(account_kill.psutil, "Process", return_value=process):
            self.assertEqual(account_kill._close((100, 1.0)), (True, ""))
        process.kill.assert_called_once()

    def test_a_reused_pid_is_never_killed_directly(self):
        failed = SimpleNamespace(returncode=1, stdout="", stderr="ERROR: denied")
        process = MagicMock()
        process.create_time.return_value = 999.0
        with self.running(True), \
                patch.object(account_kill.subprocess, "run", return_value=failed), \
                patch.object(account_kill.psutil, "Process", return_value=process), \
                patch.object(account_kill.time, "monotonic", side_effect=iter(range(0, 1000))):
            ok, detail = account_kill._close((100, 1.0))
        self.assertFalse(ok)
        process.kill.assert_not_called()
        self.assertIn("denied", detail)

    def test_it_gives_up_after_a_few_attempts(self):
        failed = SimpleNamespace(returncode=1, stdout="", stderr="stuck")
        with self.running(True), \
                patch.object(account_kill.subprocess, "run", return_value=failed) as run, \
                patch.object(account_kill.psutil, "Process", side_effect=psutil.NoSuchProcess(100)), \
                patch.object(account_kill.time, "monotonic", side_effect=iter(range(0, 1000))):
            ok, _ = account_kill._close((100, 1.0))
        self.assertFalse(ok)
        self.assertEqual(run.call_count, account_kill._ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
