import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from src.config.config import Config, parse_daily_account_suffixes
from src.core import tasks
from src.core.exceptions import StopError
from src.core.geometry import TextBox
from src.core.message import MsgTaskStatus, MsgType
from src.core.runtime import RuntimeConfig
from src.core.task import TaskFSM
from src.core.workflow import NodeContext, TaskSpec
from src.service.daily_workflow import endNode
from src.service.saved_accounts import SavedAccountSwitcher, _AccountPanel


class AccountSelectionTest(unittest.TestCase):
    def setUp(self):
        self.switcher = SavedAccountSwitcher(SimpleNamespace())
        self.switcher.ui = Mock()
        self.switcher.ui.wait.return_value.until.side_effect = lambda match: match()
        self.switcher.ui.sleep.return_value = self.switcher.ui
        self.switcher._click = Mock()
        self.switcher._enter_world = Mock(return_value=True)
        self.login = TextBox(100, 290, 140, 320, "Login", 1)

    @staticmethod
    def row(suffix, y):
        return TextBox(80, y, 160, y + 20, f"188****{suffix}", 1)

    def pick(self, current, requested, verified=None):
        field = self.row(current, 80)
        target = self.row(requested, 160)
        self.switcher._open_panel = Mock(return_value=_AccountPanel(42, self.login, (field,)))
        self.switcher._read_panel = Mock(side_effect=[
            _AccountPanel(42, None, (field, self.row(current, 120), target)),
            _AccountPanel(42, self.login, (self.row(verified or requested, 80),)),
        ])
        self.switcher.enter(requested)
        self.assertEqual(self.switcher._click.call_args_list,
                         [call(42, field.center), call(42, target.center), call(42, self.login.center)])

    def test_selects_both_directions_and_verifies_before_login(self):
        self.pick("1001", "1002")
        self.switcher._click.reset_mock()
        self.pick("1002", "1001")

    def test_wrong_selection_never_submits_login(self):
        with self.assertRaisesRegex(RuntimeError, "Could not verify"):
            self.pick("1001", "1002", verified="1001")
        self.assertEqual(self.switcher._click.call_count, 2)
        self.switcher._enter_world.assert_not_called()

    def test_duplicate_visible_suffix_never_selects_a_row(self):
        field = self.row("1001", 80)
        self.switcher._open_panel = Mock(return_value=_AccountPanel(42, self.login, (field,)))
        self.switcher._read_panel = Mock(return_value=_AccountPanel(
            42, None, (field, self.row("1002", 120), self.row("1002", 160))))
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            self.switcher.enter("1002")
        self.switcher._click.assert_called_once_with(42, field.center)

    def test_saved_login_click_uses_child_client_coordinates(self):
        control = Mock()
        ctx = SimpleNamespace(control_service=control, window_service=SimpleNamespace(window=42))
        switcher = SavedAccountSwitcher(ctx)
        switcher.ui = Mock()
        with (patch("win32gui.ChildWindowFromPointEx", return_value=44),
              patch("win32gui.ClientToScreen", return_value=(500, 600)),
              patch("win32gui.ScreenToClient", return_value=(10, 12))):
            switcher._click(43, (100, 150))
        control.activate_window.assert_called_once_with(43)
        control.click_window.assert_called_once_with(44, 10, 12)


class DailyAccountRotationTest(unittest.TestCase):
    def setUp(self):
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.ctx = NodeContext(ipc=Mock())
        self.ctx.runtime.stop_event = self.stop_event
        self.ctx.runtime.send = Mock()
        self.switcher = self.enterContext(patch("src.service.saved_accounts.SavedAccountSwitcher")).return_value
        self.create_workflow = self.enterContext(patch("src.service.daily_workflow.DailyWorkflow"))
        self.release = self.enterContext(patch("src.core.tasks.release_press_key"))

    def test_rotation_resets_daily_state_and_reports_once_at_end(self):
        self.ctx.spec = TaskSpec(user_config=Config.from_dict({"Daily": {
            "MultiAccount": True, "AccountSuffixes": "1001，1002", "StartGame": False,
        }}).to_dict())
        self.ctx.runtime.cfg = RuntimeConfig(self.ctx.spec.user_config)
        self.enterContext(patch.object(tasks, "task_init", return_value=(self.ctx, Mock())))
        self.enterContext(patch.object(tasks, "create_parent_monitor"))
        self.enterContext(patch.object(tasks, "create_mouse_reset_monitor"))
        self.enterContext(patch.object(tasks.keymouse_util, "get_mouse_position", return_value=(0, 0)))
        self.ctx._container = Mock()
        states = []

        def create(ctx):
            self.assertIsNone(ctx.shared.team_members)
            states.append(ctx.shared)
            workflow = Mock()

            def run():
                self.assertTrue(workflow.embedding)
                self.assertFalse(ctx.ipc.event_queue.put.called)
                ctx.shared.team_members = ["member"]
                return True

            workflow.execute.side_effect = run
            return workflow

        self.create_workflow.side_effect = create
        tasks.daily_task(self.stop_event, self.ctx.spec, self.ctx.ipc)
        self.assertEqual(self.switcher.enter.call_args_list, [call("1001"), call("1002")])
        self.assertEqual(len(states), 2)
        self.assertIsNot(states[0], states[1])
        self.ctx.ipc.event_queue.put.assert_called_once_with({"task": {"DailyTask": "finished"}}, block=True)
        self.assertEqual(self.ctx.runtime.send.call_args_list, [
            call(MsgType.TASK_STATUS, status=MsgTaskStatus.RUNNING),
            call(MsgType.TASK_STATUS, status=MsgTaskStatus.SUCCESS),
        ])

    def test_normal_daily_failure_does_not_skip_the_next_verified_account(self):
        self.create_workflow.side_effect = [Mock(execute=lambda: False), Mock(execute=lambda: True)]
        with self.assertLogs("src.core.tasks", "WARNING"):
            self.assertFalse(tasks._run_daily_accounts(self.ctx, ("1001", "1002")))
        self.assertEqual(self.switcher.enter.call_args_list, [call("1001"), call("1002")])
        self.assertEqual(self.release.call_count, 2)

    def test_switch_failure_does_not_run_dailies_on_unverified_account(self):
        self.switcher.enter.side_effect = RuntimeError("Selection did not match")
        with self.assertRaisesRegex(RuntimeError, "Selection did not match"):
            tasks._run_daily_accounts(self.ctx, ("1001", "1002"))
        self.switcher.enter.assert_called_once_with("1001")
        self.create_workflow.assert_not_called()

    def test_stop_releases_controls_and_prevents_next_switch(self):
        def stop():
            self.stop_event.clear()
            return False

        self.create_workflow.return_value.execute.side_effect = stop
        with self.assertRaises(StopError):
            tasks._run_daily_accounts(self.ctx, ("1001", "1002"))
        self.switcher.enter.assert_called_once_with("1001")
        self.release.assert_called_once_with(self.ctx)

    def test_embedded_daily_finishes_without_ending_parent_task(self):
        self.ctx.runtime.taskFSM = TaskFSM()
        self.ctx.runtime.taskFSM.start()
        endNode(self.ctx, SimpleNamespace(rootFSM=SimpleNamespace(is_finished=True)), embedded=True)
        self.assertTrue(self.ctx.runtime.taskFSM.is_finished)
        self.ctx.runtime.send.assert_not_called()
        self.ctx.ipc.event_queue.put.assert_not_called()


class DailyAccountConfigTest(unittest.TestCase):
    def test_existing_settings_default_to_single_account(self):
        self.assertFalse(RuntimeConfig(Config()).daily.multiAccount)

    def test_config_preserves_order_and_leading_zero(self):
        self.assertEqual(parse_daily_account_suffixes("1002， 0001、1003"), ("1002", "0001", "1003"))

    def test_duplicate_or_malformed_suffixes_are_rejected(self):
        for value in ("", "1001", "1001,1001", "1001,10102", "1001,abcd"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_daily_account_suffixes(value)
