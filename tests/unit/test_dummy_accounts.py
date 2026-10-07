import threading
import unittest
from unittest.mock import patch

from classes.operation_result import OperationResult
from features import dummy_accounts


class FakeManager:
    def __init__(self, accounts):
        self.accounts = accounts
        self._accounts_lock = threading.RLock()


class FakeRobloxFile:
    """Stands in for GlobalBasicSettings_13.xml and the UI settings store."""

    def __init__(self, framerate="144", quality="7"):
        self.values = {"FramerateCap": framerate, "SavedQualityLevel": quality}
        self.store = {}
        self.writes = []

    def load_settings(self):
        return OperationResult.success(data={
            "settings": [{"key": k, "value": v} for k, v in self.values.items()],
        })

    def apply_settings(self, changes, expected_hash="", framerate_locked=None):
        self.writes.append(dict(changes))
        self.values.update(changes)
        return OperationResult.success()

    def get(self, key, default=None):
        return self.store.get(key, default)

    def save(self, key, value):
        self.store[key] = value
        return True

    def remove(self, key):
        return self.store.pop(key, None) is not None


class DummyProfileTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRobloxFile()
        self.manager = FakeManager({
            "dummy_user": {"dummy": True},
            "main_user": {},
        })
        dummy_accounts._last_profile = None
        dummy_accounts._last_apply_time = 0.0
        patches = [
            patch.object(dummy_accounts.roblox_settings_mod, "load_settings", self.fake.load_settings),
            patch.object(dummy_accounts.roblox_settings_mod, "apply_settings", self.fake.apply_settings),
            patch.object(
                dummy_accounts.roblox_settings_mod,
                "apply_saved_customizations",
                lambda: OperationResult.success(),
            ),
            patch.object(dummy_accounts.settings_store_mod, "get", self.fake.get),
            patch.object(dummy_accounts.settings_store_mod, "save", self.fake.save),
            patch.object(dummy_accounts.settings_store_mod, "remove", self.fake.remove),
            patch.object(dummy_accounts.time, "sleep", lambda _s: None),
            patch.object(dummy_accounts, "_available_ram_mb", lambda: 99999.0),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_dummy_launch_sets_low_values_and_remembers_normal_ones(self):
        result = dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
        self.assertTrue(result)
        self.assertEqual(self.fake.values["FramerateCap"], "5")
        self.assertEqual(self.fake.values["SavedQualityLevel"], "1")
        self.assertEqual(
            self.fake.store["dummy_restore_snapshot"],
            {"FramerateCap": "144", "SavedQualityLevel": "7"},
        )

    def test_normal_launch_after_dummy_restores_original_values(self):
        dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
        result = dummy_accounts.apply_launch_profile(self.manager, "main_user")
        self.assertTrue(result)
        self.assertEqual(self.fake.values["FramerateCap"], "144")
        self.assertEqual(self.fake.values["SavedQualityLevel"], "7")
        self.assertNotIn("dummy_restore_snapshot", self.fake.store)

    def test_second_dummy_launch_keeps_the_original_snapshot(self):
        dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
        dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
        self.assertEqual(
            self.fake.store["dummy_restore_snapshot"],
            {"FramerateCap": "144", "SavedQualityLevel": "7"},
        )

    def test_normal_launch_without_dummy_history_writes_nothing(self):
        dummy_accounts.apply_launch_profile(self.manager, "main_user")
        self.assertEqual(self.fake.writes, [])

    def test_switching_profiles_waits_for_the_settle_time(self):
        sleeps = []
        with patch.object(dummy_accounts.time, "sleep", sleeps.append):
            dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
            dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
            self.assertEqual(sleeps, [])
            dummy_accounts.apply_launch_profile(self.manager, "main_user")
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0)

    def test_settings_are_clamped_to_safe_ranges(self):
        self.fake.store["dummy_framerate_cap"] = 0
        self.fake.store["dummy_ram_limit_mb"] = 999999
        cfg = dummy_accounts.get_dummy_settings()
        self.assertEqual(cfg["dummy_framerate_cap"], 1)
        self.assertEqual(cfg["dummy_ram_limit_mb"], 8192)

    def test_unknown_or_missing_account_is_treated_as_normal(self):
        self.assertFalse(dummy_accounts.is_dummy_account(self.manager, None))
        self.assertFalse(dummy_accounts.is_dummy_account(self.manager, "nobody"))
        self.assertTrue(dummy_accounts.is_dummy_account(self.manager, "dummy_user"))


class RamGuardTests(unittest.TestCase):
    def setUp(self):
        self.store = {"dummy_min_free_ram_mb": 2500}
        self.manager = FakeManager({"dummy_user": {"dummy": True}, "main_user": {}})
        dummy_accounts._last_profile = None
        self.applied = []
        self.clock = [0.0]
        self.sleeps = []

        def fake_sleep(seconds):
            self.sleeps.append(seconds)
            self.clock[0] += seconds

        patches = [
            patch.object(dummy_accounts.settings_store_mod, "get", lambda k, d=None: self.store.get(k, d)),
            patch.object(dummy_accounts.time, "monotonic", lambda: self.clock[0]),
            patch.object(dummy_accounts.time, "sleep", fake_sleep),
            patch.object(
                dummy_accounts,
                "_apply_dummy_profile",
                lambda: (self.applied.append("dummy") or OperationResult.success()),
            ),
            patch.object(
                dummy_accounts,
                "_apply_normal_profile",
                lambda: (self.applied.append("normal") or OperationResult.success()),
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def set_available(self, *values):
        readings = list(values)
        patcher = patch.object(
            dummy_accounts,
            "_available_ram_mb",
            lambda: readings.pop(0) if len(readings) > 1 else readings[0],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_launch_proceeds_immediately_when_enough_ram_is_free(self):
        self.set_available(8000.0)
        self.assertTrue(dummy_accounts.apply_launch_profile(self.manager, "dummy_user"))
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.applied, ["dummy"])

    def test_launch_waits_until_memory_frees_up(self):
        self.set_available(1000.0, 1200.0, 3000.0)
        self.assertTrue(dummy_accounts.apply_launch_profile(self.manager, "dummy_user"))
        self.assertEqual(len(self.sleeps), 2)
        self.assertEqual(self.applied, ["dummy"])

    def test_launch_is_refused_when_memory_never_frees_up(self):
        self.set_available(500.0)
        result = dummy_accounts.apply_launch_profile(self.manager, "dummy_user")
        self.assertFalse(result)
        self.assertEqual(result.code, "LOW_FREE_RAM")
        self.assertEqual(self.applied, [])

    def test_guard_can_be_turned_off(self):
        self.store["dummy_min_free_ram_mb"] = 0
        self.set_available(10.0)
        self.assertTrue(dummy_accounts.apply_launch_profile(self.manager, "dummy_user"))
        self.assertEqual(self.sleeps, [])

    def test_normal_accounts_are_never_held_back(self):
        self.set_available(10.0)
        self.assertTrue(dummy_accounts.apply_launch_profile(self.manager, "main_user"))
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.applied, ["normal"])


class FakeProcess:
    def __init__(self, rss_mb):
        self._rss = int(rss_mb * 1024 * 1024)

    def memory_info(self):
        return type("Mem", (), {"rss": self._rss})()


class TrimmerTests(unittest.TestCase):
    OLD = 1.0

    def setUp(self):
        self.store = {"dummy_ram_trim_delay_sec": 90, "dummy_ram_limit_mb": 300}
        self.manager = FakeManager({
            "dummy_user": {"dummy": True, "user_id": "1"},
            "main_user": {"user_id": "2"},
        })
        self.processes = {
            100: (self.OLD, FakeProcess(2000)),
            200: (self.OLD, FakeProcess(2000)),
        }
        self.pid_users = {100: "1", 200: "2"}
        self.trimmed, self.capped, self.released = [], [], []
        presence = dummy_accounts.presence_mod
        patches = [
            patch.object(dummy_accounts.settings_store_mod, "get", lambda k, d=None: self.store.get(k, d)),
            patch.object(presence, "get_roblox_processes", lambda: dict(self.processes)),
            patch.object(presence, "_get_user_id_from_pid", lambda pid, used=None: self.pid_users.get(pid)),
            patch.object(dummy_accounts, "_empty_working_set", lambda pid: (self.trimmed.append(pid) or True, "")),
            patch.object(
                dummy_accounts,
                "_apply_hard_working_set",
                lambda pid, mb: (self.capped.append((pid, mb)) or True, ""),
            ),
            patch.object(
                dummy_accounts,
                "_release_hard_working_set",
                lambda pid: (self.released.append(pid) or True, ""),
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.trimmer = dummy_accounts.DummyRamTrimmer(self.manager)

    def test_only_dummy_windows_are_trimmed(self):
        self.trimmer.scan_once()
        self.assertEqual(self.trimmed, [100])
        self.assertEqual(self.capped, [])

    def test_windows_below_the_limit_are_left_alone(self):
        self.processes[100] = (self.OLD, FakeProcess(250))
        self.trimmer.scan_once()
        self.assertEqual(self.trimmed, [])

    def test_new_windows_wait_for_the_trim_delay(self):
        self.processes[100] = (dummy_accounts.time.time(), FakeProcess(2000))
        self.trimmer.scan_once()
        self.assertEqual(self.trimmed, [])

    def test_hard_limit_caps_once_and_skips_periodic_trim(self):
        self.store["dummy_ram_hard_limit"] = True
        self.trimmer.scan_once()
        self.trimmer.scan_once()
        self.assertEqual(self.capped, [(100, 300)])
        self.assertEqual(self.trimmed, [])

    def test_turning_the_hard_limit_off_releases_it(self):
        self.store["dummy_ram_hard_limit"] = True
        self.trimmer.scan_once()
        self.store["dummy_ram_hard_limit"] = False
        self.trimmer.scan_once()
        self.assertEqual(self.released, [100])
        self.assertEqual(self.trimmed, [100])

    def test_unticking_a_running_account_releases_its_cap(self):
        self.store["dummy_ram_hard_limit"] = True
        self.trimmer.scan_once()
        self.manager.accounts["dummy_user"].pop("dummy")
        self.manager.accounts["main_user"]["dummy"] = True
        self.trimmer.scan_once()
        self.assertEqual(self.released, [100])
        self.assertIn((200, 300), self.capped)


if __name__ == "__main__":
    unittest.main()
