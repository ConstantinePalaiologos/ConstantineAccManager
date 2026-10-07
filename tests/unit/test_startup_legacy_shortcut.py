import os
import tempfile
import unittest
from unittest.mock import patch

from features import windows_startup


class LegacyShortcutTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = patch.object(windows_startup, "get_startup_folder", lambda: self.folder.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def touch(self, name):
        path = os.path.join(self.folder.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
        return path

    def test_new_shortcut_name_is_not_the_old_one(self):
        self.assertNotIn("Evanovar", windows_startup.get_shortcut_path())
        self.assertIn("Constantine", windows_startup.get_shortcut_path())

    def test_old_named_shortcut_is_removed(self):
        legacy = self.touch(windows_startup._LEGACY_SHORTCUT_NAME)
        windows_startup._remove_legacy_shortcut()
        self.assertFalse(os.path.exists(legacy))

    def test_other_shortcuts_are_left_alone(self):
        other = self.touch("Something Else.lnk")
        windows_startup._remove_legacy_shortcut()
        self.assertTrue(os.path.exists(other))

    def test_turning_startup_off_also_removes_the_old_shortcut(self):
        legacy = self.touch(windows_startup._LEGACY_SHORTCUT_NAME)
        current = self.touch(os.path.basename(windows_startup.get_shortcut_path()))
        result = windows_startup.disable_startup()
        self.assertTrue(result)
        self.assertFalse(os.path.exists(legacy))
        self.assertFalse(os.path.exists(current))


if __name__ == "__main__":
    unittest.main()
