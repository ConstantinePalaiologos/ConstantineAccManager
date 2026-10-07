import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPushButton, QWidgetAction

from classes.operation_result import OperationResult
from utils import ui


class ImmediateThread:
    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()


class DangerActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, text="Kill"):
        menu = QMenu()
        self.addCleanup(menu.close)
        self.addCleanup(menu.deleteLater)
        triggered = []
        action = ui._add_danger_action(menu, text, "tip")
        action.triggered.connect(lambda *_: triggered.append(True))
        return menu, action, triggered

    def test_it_is_a_red_item_inside_the_menu(self):
        menu, action, _ = self.make()
        self.assertIsInstance(action, QWidgetAction)
        self.assertIn(action, menu.actions())
        button = action.defaultWidget()
        self.assertIsInstance(button, QPushButton)
        self.assertEqual(button.text(), "Kill")
        self.assertIn(ui.DANGER_COLOR.lower(), button.styleSheet().lower())
        self.assertEqual(button.toolTip(), "tip")

    def test_clicking_it_runs_the_action_once_and_closes_the_menu(self):
        menu, action, triggered = self.make()
        menu.popup(menu.pos())
        self.app.processEvents()
        self.assertTrue(menu.isVisible())
        action.defaultWidget().click()
        self.app.processEvents()
        self.assertEqual(triggered, [True])
        self.assertFalse(menu.isVisible())

    def test_the_label_can_show_how_many_accounts(self):
        _, action, _ = self.make("Kill (3 accounts)")
        self.assertEqual(action.defaultWidget().text(), "Kill (3 accounts)")


class KillHandlerTests(unittest.TestCase):
    def setUp(self):
        self.emitted = []
        self.window = SimpleNamespace(
            manager=SimpleNamespace(accounts={"alpha": {}, "beta": {}}),
            _bridge=SimpleNamespace(kill_done=SimpleNamespace(emit=self.emitted.append)),
            _ar_workers={},
            _show_operation_error=MagicMock(),
        )

    def start(self, names):
        ok = OperationResult.success("Closed 1 Roblox window.", data={"closed": {"alpha": 1}})
        with patch.object(ui.threading, "Thread", ImmediateThread), \
                patch.object(ui.account_kill_mod, "kill_accounts", return_value=ok) as kill:
            ui.AccountManagerUIQt._on_kill_accounts(self.window, names)
        return kill

    def test_one_account_is_closed_without_asking(self):
        with patch.object(ui.QMessageBox, "question") as question:
            kill = self.start(["alpha"])
        question.assert_not_called()
        self.assertEqual(kill.call_args.args[1], ["alpha"])
        self.assertEqual(len(self.emitted), 1)

    def test_several_accounts_ask_first_and_do_nothing_on_no(self):
        with patch.object(ui.QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            kill = self.start(["alpha", "beta"])
        kill.assert_not_called()
        self.assertEqual(self.emitted, [])

    def test_several_accounts_proceed_on_yes(self):
        with patch.object(ui.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            kill = self.start(["alpha", "beta"])
        self.assertEqual(kill.call_args.args[1], ["alpha", "beta"])
        self.assertEqual(len(self.emitted), 1)

    def test_accounts_that_no_longer_exist_are_ignored(self):
        with patch.object(ui.QMessageBox, "question") as question:
            kill = self.start(["ghost"])
        kill.assert_not_called()
        question.assert_not_called()
        self.assertEqual(self.emitted, [])

    def test_a_crash_in_the_worker_becomes_an_error_result_not_a_hang(self):
        with patch.object(ui.threading, "Thread", ImmediateThread), \
                patch.object(ui.account_kill_mod, "kill_accounts", side_effect=RuntimeError("boom")):
            ui.AccountManagerUIQt._on_kill_accounts(self.window, ["alpha"])
        self.assertEqual(len(self.emitted), 1)
        self.assertFalse(self.emitted[0])

    def test_a_failure_is_shown_as_an_error(self):
        failure = OperationResult.failure("ROBLOX_ACCOUNT_KILL_FAILED", "Roblox Window Could Not Be Closed", "x")
        with patch.object(ui, "_show_info") as info:
            ui.AccountManagerUIQt._on_kill_done(self.window, failure)
        self.window._show_operation_error.assert_called_once_with(failure)
        info.assert_not_called()

    def test_an_account_with_no_window_gets_a_short_notice(self):
        result = OperationResult.success("none", data={"closed": {}, "not_running": ["alpha"]})
        with patch.object(ui, "_show_info") as info:
            ui.AccountManagerUIQt._on_kill_done(self.window, result)
        self.assertIn("alpha", info.call_args.args[2])
        self.window._show_operation_error.assert_not_called()

    def test_a_normal_success_shows_nothing(self):
        result = OperationResult.success("ok", data={"closed": {"alpha": 1}, "not_running": []})
        with patch.object(ui, "_show_info") as info:
            ui.AccountManagerUIQt._on_kill_done(self.window, result)
        info.assert_not_called()
        self.window._show_operation_error.assert_not_called()

    def test_it_warns_when_auto_rejoin_will_reopen_the_window(self):
        self.window._ar_workers = {"alpha": SimpleNamespace(is_alive=lambda: True)}
        result = OperationResult.success("ok", data={"closed": {"alpha": 1}, "not_running": []})
        with patch.object(ui, "_show_info") as info:
            ui.AccountManagerUIQt._on_kill_done(self.window, result)
        info.assert_called_once()
        self.assertIn("Auto-Rejoin", info.call_args.args[1])
        self.assertIn("alpha", info.call_args.args[2])

    def test_a_stopped_auto_rejoin_worker_is_not_mentioned(self):
        self.window._ar_workers = {"alpha": SimpleNamespace(is_alive=lambda: False)}
        result = OperationResult.success("ok", data={"closed": {"alpha": 1}, "not_running": []})
        with patch.object(ui, "_show_info") as info:
            ui.AccountManagerUIQt._on_kill_done(self.window, result)
        info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
