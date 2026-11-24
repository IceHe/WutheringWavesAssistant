import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.config.config import Config, parse_daily_account_suffixes
from src.core import tasks
from src.core.exceptions import AccountLoggedInElsewhere, StopError
from src.core.geometry import TextBox
from src.core.i18n import I18nText, I18nTr, Language
from src.core.message import MsgTaskStatus, MsgType
from src.core.pages import OcrQuery, OcrResult
from src.core.runtime import RuntimeConfig
from src.core.task import TaskFSM
from src.core.workflow import NodeContext, TaskSpec
from src.service.daily_workflow import endNode
from src.service.saved_accounts import SavedAccountLoginTimeout, SavedAccountSwitcher, _AccountPanel, _suffix


def disconnect_dialog(reason="账号在别处登录"):
    return OcrResult([
        TextBox(376, 280, 510, 307, "连接已断开", 1),
        TextBox(708, 422, 879, 444, reason, 1),
        TextBox(1026, 553, 1074, 576, "确认", 1),
    ])


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

    def test_qr_login_from_ocr_accepts_a_bare_identifier_and_readback(self):
        field = self.row("1001", 80)
        target = TextBox(80, 160, 160, 180, "Ａ12ｂ", 1)
        self.switcher._open_panel = Mock(return_value=_AccountPanel(42, self.login, (field,)))
        self.switcher._read_panel = Mock(side_effect=[
            _AccountPanel(42, None, (field, self.row("1001", 120), target)),
            _AccountPanel(42, self.login, (TextBox(80, 80, 160, 100, "a12b", 1),)),
        ])
        self.switcher.enter("A12B")
        self.assertEqual(self.switcher._click.call_args_list,
                         [call(42, field.center), call(42, target.center), call(42, self.login.center)])

    def test_saved_account_suffixes_match_qr_masks_and_ignore_plain_ui_text(self):
        self.assertEqual(_suffix("扫码用户****a12b", ocr=True), "A12B")
        self.assertEqual(_suffix("扫码用户a12b", ocr=True), "A12B")
        self.assertEqual(_suffix("Saved QR account A12B"), "A12B")
        self.assertEqual(_suffix("abcd", ocr=True, configured={"ABCD"}), "ABCD")
        self.assertIsNone(_suffix("Help", ocr=True, configured={"ABCD", "A12B"}))
        self.assertIsNone(_suffix("verification 1001", ocr=True, configured={"1001"}))

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

    def test_login_resubmits_at_most_once_if_the_verified_panel_remains(self):
        panel = _AccountPanel(42, self.login, (self.row("1001", 80),))
        self.switcher._open_panel = Mock(return_value=panel)
        self.switcher._select_visible = Mock()
        self.switcher._read_panel = Mock(side_effect=[panel, panel])
        self.switcher._enter_world.side_effect = [False, False, False, True]
        self.switcher.ui.wait.return_value.until.side_effect = lambda check: check() or check() or check() or check()

        with patch("src.service.saved_accounts.time.monotonic", side_effect=[0, 3, 8, 16]):
            self.switcher.enter("1001")

        self.assertEqual(self.switcher._click.call_args_list, [call(42, self.login.center)] * 2)

    def test_login_timeout_does_not_resubmit_a_different_account_or_window(self):
        field = self.row("1001", 80)
        panel = _AccountPanel(42, self.login, (field,))
        self.switcher._open_panel = Mock(return_value=panel)
        self.switcher._select_visible = Mock()
        self.switcher._enter_world.return_value = False
        for changed_panel in (_AccountPanel(42, self.login, (self.row("1002", 80),)),
                              _AccountPanel(43, self.login, (field,)), None):
            with self.subTest(changed_panel=changed_panel):
                self.switcher._click.reset_mock()
                self.switcher._read_panel = Mock(side_effect=[panel, changed_panel])
                with (patch("src.service.saved_accounts.time.monotonic", side_effect=[0, 8]),
                      self.assertRaises(SavedAccountLoginTimeout)):
                    self.switcher.enter("1001")
                self.switcher._click.assert_called_once_with(42, self.login.center)

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


class ReturnToLoginTest(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        self.ctx = SimpleNamespace(tr=I18nTr(Language.ZH), ocr_service=Mock())
        self.switcher = SavedAccountSwitcher(self.ctx)
        self.ui = self.switcher.ui = Mock()
        self.ui.grap.return_value = self.frame
        self.ui.sleep.return_value = self.ui
        self.ui.wait.return_value.until.side_effect = lambda read: read()
        self.panel = _AccountPanel(42, None, ())
        self.switcher._read_panel = Mock(side_effect=[None, self.panel])
        # The two choices and prompt at the positions observed in the game client.
        self.quit = TextBox(488, 550, 593, 580, "退出游戏", 1)
        self.login = TextBox(1000, 550, 1110, 580, "返回登录", 1)
        self.prompt = TextBox(685, 416, 907, 449, "是否确定退出游戏？", 1)
        self.exit_dialog = OcrResult([self.quit, self.login, self.prompt])
        self.logout_prompt = TextBox(708, 422, 871, 445, "确认登出账号？", 1)
        self.logout_confirm = TextBox(1001, 553, 1099, 577, "确认登出", 1)
        self.logout_dialog = OcrResult([self.logout_prompt, self.logout_confirm])

    def test_terminal_clicks_power_then_return_to_login(self):
        terminal = OcrResult([TextBox(20, 50, 120, 78, self.ctx.tr(key).raw, 1) for key in (
            I18nText.Events, I18nText.SOL3Phase, I18nText.UnionLevel, I18nText.UnionEXP,
        )])
        self.ctx.ocr_service.query.side_effect = [terminal, self.exit_dialog]
        self.assertIs(self.switcher._open_panel(), self.panel)
        self.ui.click.assert_called_once_with(64, 864)
        self.ui.click_bbox.assert_called_once_with(self.login)

    def test_resumes_when_the_exit_dialog_is_already_open(self):
        self.ctx.ocr_service.query.return_value = self.exit_dialog
        self.assertIs(self.switcher._open_panel(), self.panel)
        self.ui.click_bbox.assert_called_once_with(self.login)
        self.ui.click.assert_not_called()
        self.ui.esc.assert_not_called()

    def test_claims_lunite_reward_before_trying_to_return_to_login(self):
        claim = TextBox(610, 730, 970, 770, "点击领取今日月相观测卡奖励", 1)
        self.ctx.ocr_service.query.side_effect = [OcrResult([claim]), self.exit_dialog]

        def wait_until(read):
            return read() or read()

        self.ui.wait.return_value.until.side_effect = wait_until
        self.assertIs(self.switcher._open_panel(), self.panel)
        self.ui.click.assert_called_once_with(*claim.center, delay=0.2)
        self.ui.click_bbox.assert_called_once_with(self.login)
        self.ui.esc.assert_not_called()

    def test_generic_quit_confirmation_cannot_close_game(self):
        cancel = TextBox(488, 550, 593, 580, "取消", 1)
        confirm = TextBox(1000, 550, 1110, 580, "确认", 1)
        result = OcrResult([self.prompt, cancel, confirm])
        self.assertIsNone(self.switcher._return_login_button(self.frame, result))

        prompt = TextBox(685, 416, 907, 449, "是否返回登录界面？", 1)
        result = OcrResult([prompt, cancel, confirm])
        self.assertIs(self.switcher._return_login_button(self.frame, result), confirm)

    def test_ignores_an_isolated_login_label(self):
        self.assertIsNone(self.switcher._return_login_button(self.frame, OcrResult([self.login])))

    def test_resumes_when_logout_confirmation_is_already_open(self):
        self.ctx.ocr_service.query.return_value = self.logout_dialog

        self.assertIs(self.switcher._open_panel(), self.panel)

        self.ui.click_bbox.assert_called_once_with(self.logout_confirm, delay=0.2)
        self.ui.esc.assert_not_called()

    def test_confirms_logout_then_opens_login_and_selects_the_configured_account(self):
        settings = TextBox(1480, 550, 1550, 580, "设置", 1)
        account = TextBox(1480, 430, 1550, 460, "账号", 1)
        login = TextBox(765, 830, 830, 860, "登入", 1)
        title = OcrResult([settings, account])
        self.ctx.ocr_service.query.side_effect = [title, title, self.logout_dialog, OcrResult([settings, account, login])]
        submit = TextBox(100, 290, 140, 320, "登录", 1)
        current = TextBox(80, 80, 160, 100, "188****1001", 1)
        target = TextBox(80, 140, 160, 160, "188****1002", 1)
        self.switcher._read_panel.side_effect = [
            None, None, None, None,
            _AccountPanel(42, submit, (current,)),
            _AccountPanel(42, None, (current, target)),
            _AccountPanel(42, submit, (target,)),
        ]
        self.ui.wait.return_value.until.side_effect = lambda read: read() or read() or read() or read()
        self.switcher._click = self.ui.saved_account_click

        with patch.object(self.switcher, "_enter_world", return_value=True):
            self.switcher.enter("1002")

        click_names = {"click_point", "click_bbox", "saved_account_click"}
        self.assertEqual([action for action in self.ui.mock_calls if action[0] in click_names], [
            call.click_point(account.center),
            call.click_bbox(self.logout_confirm, delay=0.2),
            call.click_point(login.center),
            call.saved_account_click(42, current.center),
            call.saved_account_click(42, target.center),
            call.saved_account_click(42, submit.center),
        ])

    def test_logout_requires_prompt_and_dedicated_button(self):
        generic = TextBox(1020, 553, 1077, 576, "确认", 1)
        self.assertFalse(self.switcher._handle_logout_dialog(self.frame, OcrResult([self.logout_confirm])))
        self.assertTrue(self.switcher._handle_logout_dialog(self.frame, OcrResult([self.logout_prompt, generic])))
        self.ui.click_bbox.assert_not_called()

    def test_account_disconnect_confirms_then_waits_for_title_screen(self):
        dialog = disconnect_dialog()
        title = OcrResult([TextBox(20, i * 40, 210, i * 40 + 20, self.ctx.tr(key).raw, 1)
                           for i, key in enumerate((I18nText.Settings, I18nText.TapToLandInSolaris3))])
        self.ctx.ocr_service.query.side_effect = [dialog, OcrResult([]), title]
        self.ui.wait.return_value.until.side_effect = lambda read: read() or read() or read()

        self.switcher.return_after_disconnect()

        self.assertEqual(self.ctx.ocr_service.query.call_count, 3)
        self.ui.click_bbox.assert_called_once_with(dialog.results[-1], delay=0.2)
        self.ui.esc.assert_not_called()

    def test_account_disconnect_during_login_takes_precedence_over_homepage(self):
        self.ctx.ocr_service.query.return_value = disconnect_dialog()
        self.ui.is_on_homepage.return_value = True
        with self.assertRaises(AccountLoggedInElsewhere):
            self.switcher._enter_world()
        self.ui.click_bbox.assert_not_called()

    def test_connection_timeout_dialog_is_dismissed_during_login(self):
        dialog = disconnect_dialog("网络连接超时")
        self.ctx.ocr_service.query.return_value = dialog

        self.assertFalse(self.switcher._enter_world())

        self.ui.click_bbox.assert_called_once_with(dialog.results[-1], delay=0.2)
        self.ui.is_on_homepage.assert_not_called()

    def test_kick_while_leaving_previous_account_still_opens_saved_accounts(self):
        terminal = OcrResult([TextBox(20, 50, 120, 78, self.ctx.tr(key).raw, 1) for key in (
            I18nText.Events, I18nText.SOL3Phase, I18nText.UnionLevel, I18nText.UnionEXP,
        )])
        dialog = disconnect_dialog()
        self.ctx.ocr_service.query.side_effect = [terminal, dialog]

        self.assertIs(self.switcher._open_panel(), self.panel)

        self.ui.click_bbox.assert_called_once_with(dialog.results[-1], delay=0.2)


class DailyAccountRotationTest(unittest.TestCase):
    def setUp(self):
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.ctx = NodeContext(ipc=Mock())
        self.ctx.runtime.stop_event = self.stop_event
        self.ctx.runtime.send = Mock()
        self.switcher_type = self.enterContext(patch("src.service.saved_accounts.SavedAccountSwitcher"))
        self.switcher = self.switcher_type.return_value
        self.create_workflow = self.enterContext(patch("src.service.daily_workflow.DailyWorkflow"))
        self.release = self.enterContext(patch("src.core.tasks.release_press_key"))

    def test_rotation_resets_daily_state_and_reports_once_at_end(self):
        self.ctx.spec = TaskSpec(user_config=Config.from_dict({"Daily": {
            "MultiAccount": True, "AccountSuffixes": "1001，ABCD、1002", "StartGame": False,
            "AccountDisabledSuffixes": ["ａｂｃｄ"], "AccountTacetSuppression": {"ABCD": "PreviousTacetField"},
        }}).to_dict())
        self.ctx.runtime.cfg = RuntimeConfig(self.ctx.spec.user_config)
        self.enterContext(patch.object(tasks, "task_init", return_value=(self.ctx, Mock())))
        self.enterContext(patch.object(tasks, "create_parent_monitor"))
        self.enterContext(patch.object(tasks, "create_mouse_reset_monitor"))
        self.enterContext(patch.object(tasks.keymouse_util, "get_mouse_position", return_value=(0, 0)))
        self.ctx._container = Mock()
        states = []

        def create(ctx, *, account_suffix):
            self.assertIsNone(ctx.shared.team_members)
            self.assertEqual(self.switcher.enter.call_args, call(account_suffix))
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
        self.switcher_type.assert_called_once_with(self.ctx, ("1001", "ABCD", "1002"))
        self.assertEqual(self.switcher.enter.call_args_list, [call("1001"), call("1002")])
        self.assertEqual(len(states), 2)
        self.assertIsNot(states[0], states[1])
        self.ctx.ipc.event_queue.put.assert_called_once_with({"task": {"DailyTask": "finished"}}, block=True)
        self.assertEqual(self.ctx.runtime.send.call_args_list, [
            call(MsgType.TASK_STATUS, status=MsgTaskStatus.RUNNING),
            call(MsgType.TASK_STATUS, status=MsgTaskStatus.SUCCESS),
        ])

    def test_no_enabled_accounts_fails_before_launching_or_running_dailies(self):
        spec = TaskSpec(user_config=Config.from_dict({"Daily": {
            "MultiAccount": True, "AccountSuffixes": "1001", "AccountDisabledSuffixes": ["1001"],
        }}).to_dict())
        self.ctx.runtime.cfg = RuntimeConfig(spec.user_config)
        with (patch.object(tasks, "task_init", return_value=(self.ctx, Mock())),
              patch.object(tasks, "PrepareTask") as prepare,
              self.assertLogs("src.core.tasks", "ERROR")):
            tasks.daily_task(self.stop_event, spec, self.ctx.ipc)
        prepare.assert_not_called()
        self.switcher_type.assert_not_called()
        self.create_workflow.assert_not_called()
        self.ctx.ipc.event_queue.put.assert_called_once_with({"task": {"DailyTask": "failed"}}, block=True)

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

    def test_login_timeout_skips_the_account_and_runs_later_dailies(self):
        timeout = SavedAccountLoginTimeout("Account ending in 1002 did not enter the game within 120 seconds")
        self.switcher.enter.side_effect = [None, timeout, None]
        self.create_workflow.return_value.execute.return_value = True

        with self.assertLogs("src.core.tasks", "WARNING") as logs:
            self.assertFalse(tasks._run_daily_accounts(self.ctx, ("1001", "1002", "1003")))

        self.assertEqual(self.switcher.enter.call_args_list, [call("1001"), call("1002"), call("1003")])
        self.assertEqual(self.create_workflow.call_args_list,
                         [call(self.ctx, account_suffix="1001"), call(self.ctx, account_suffix="1003")])
        self.assertIn("Daily rotation finished with failures for: 1002", logs.output[-1])

    def test_kicked_account_stops_before_login_with_next_account(self):
        first, second = Mock(), Mock()
        first.execute.side_effect = AccountLoggedInElsewhere()
        second.execute.return_value = True
        self.create_workflow.side_effect = [first, second]
        sequence = Mock()
        sequence.attach_mock(self.switcher, "switcher")
        sequence.attach_mock(first, "first")
        sequence.attach_mock(second, "second")
        sequence.attach_mock(self.release, "release")

        with self.assertLogs("src.core.tasks", "WARNING"):
            self.assertFalse(tasks._run_daily_accounts(self.ctx, ("1001", "1002")))

        self.assertEqual(sequence.mock_calls, [
            call.switcher.enter("1001"), call.first.execute(), call.release(self.ctx),
            call.switcher.return_after_disconnect(), call.switcher.enter("1002"),
            call.second.execute(), call.release(self.ctx),
        ])

    def test_kick_during_login_skips_the_unverified_account(self):
        self.switcher.enter.side_effect = [AccountLoggedInElsewhere(), None]
        self.create_workflow.return_value.execute.return_value = True
        with self.assertLogs("src.core.tasks", "WARNING"):
            self.assertFalse(tasks._run_daily_accounts(self.ctx, ("1001", "1002")))
        self.assertEqual(self.switcher.mock_calls,
                         [call.enter("1001"), call.return_after_disconnect(), call.enter("1002")])
        self.create_workflow.assert_called_once_with(self.ctx, account_suffix="1002")

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


class DailyDisconnectTest(unittest.TestCase):
    def test_daily_ocr_interrupts_active_combat_only_for_account_disconnect(self):
        from src.service.daily_workflow import DailyWorkflow

        ctx = NodeContext(_container=Mock())
        ctx.runtime.cfg = RuntimeConfig(Config().to_dict())
        ctx.runtime.stop_event = threading.Event()
        ctx.runtime.stop_event.set()
        ctx.window_service.tr = I18nTr(Language.ZH)
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        workflow = DailyWorkflow(ctx, account_suffix="1001")
        workflow.embedding = True
        workflow.local.combat_system = Mock()
        ocr = ctx.ocr_service.query

        def scan_for_dialogs(*args, **kwargs):
            ocr.return_value = disconnect_dialog("网络连接超时")
            OcrQuery(ctx).snapshot(img=frame)
            ocr.return_value = disconnect_dialog()
            OcrQuery(ctx).snapshot(img=frame)

        with patch.object(workflow.engine, "run", side_effect=scan_for_dialogs):
            with self.assertRaises(AccountLoggedInElsewhere):
                workflow.execute()
        workflow.local.combat_system.stop.assert_called_once_with(join=True)
        self.assertTrue(workflow.fsm.is_failed)
        self.assertFalse(ctx.runtime.watch_account_disconnect)
        OcrQuery(ctx).snapshot(img=frame)


class DailyAccountConfigTest(unittest.TestCase):
    def test_existing_settings_default_to_single_account(self):
        self.assertFalse(RuntimeConfig(Config()).daily.multiAccount)

    def test_config_preserves_order_and_leading_zero(self):
        self.assertEqual(parse_daily_account_suffixes("1002， 0001、a12ｂ,abcd"), ("1002", "0001", "A12B", "ABCD"))
        self.assertEqual(parse_daily_account_suffixes("0001"), ("0001",))

    def test_duplicate_or_malformed_suffixes_are_rejected(self):
        for value in ("", "1001,1001", "a12b,A12B", "1001,10102", "1001,ab-c"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_daily_account_suffixes(value)

    def test_each_account_dispatches_only_its_selected_nests(self):
        from src.core.dungeon import Dungeon
        from src.service.daily_workflow import DailyWorkflow, doMaterialCollection

        simulacrum = I18nText.SimulacrumNexusTacetDiscordNest
        southern = I18nText.SouthernYuanHillsTacetDiscordNest
        stagnant = I18nText.StagnantRunTacetDiscordNest
        overrides = {"1001": "All", "1002": [stagnant], "a12b": [simulacrum, stagnant], "1004": []}
        ctx = NodeContext()
        for global_enabled in (True, False):
            ctx.runtime.cfg = RuntimeConfig(Config.from_dict({"Daily": {
                "TacetDiscordNest": I18nText.Mengzhou,
                "TacetDiscordNestOpen": global_enabled,
                "AccountTacetDiscordNest": overrides,
            }}).to_dict())
            inherited = [simulacrum, southern] if global_enabled else []
            for suffix, expected in (
                ("1001", list(Dungeon.TacetDiscordNest)), ("1002", [stagnant]),
                ("A12B", [simulacrum, stagnant]), ("1004", []), ("1005", inherited), (None, inherited),
            ):
                with self.subTest(global_enabled=global_enabled, suffix=suffix):
                    local = DailyWorkflow(ctx, account_suffix=suffix).local
                    self.assertEqual([nest.name for nest in local.tacetDiscordNestFSM.children if nest.is_active],
                                     expected)
                    self.assertEqual(doMaterialCollection(ctx, local), I18nText.TacetDiscordNest if expected else None)

    def test_workflow_chooses_one_account_field_and_uses_global_fallback(self):
        from src.service.daily_workflow import DailyWorkflow

        stillness = I18nText.TacetFieldHeartOfStillness
        flames = I18nText.TacetFieldHeartOfFlames
        values = {
            "AccountTacetSuppression": {"6663": stillness, "917a": flames},
            "TacetSuppression": stillness,
            "TacetSuppressionOpen": True,
        }
        ctx = NodeContext()
        ctx.runtime.cfg = RuntimeConfig(Config.from_dict({"Daily": values}).to_dict())
        for suffix, expected in (("6663", stillness), ("917A", flames), ("9892", stillness), (None, stillness)):
            with self.subTest(suffix=suffix):
                local = DailyWorkflow(ctx, account_suffix=suffix).local
                self.assertEqual(local.account_suffix, suffix)
                self.assertEqual([field.name for field in local.tacetSuppressionFSM.children if field.is_active],
                                 [expected])
        for material, target, group in (
            ("ForgeryChallenge", I18nText.WingfallChasm, "forgeryChallengeFSM"),
            ("BossChallenge", I18nText.EnemyCalamityEffigy, "bossChallengeFSM"),
        ):
            with self.subTest(material=material):
                settings = dict(values, TacetSuppressionOpen=False)
                settings[f"{material}Open"] = True
                settings[material] = target
                ctx.runtime.cfg = RuntimeConfig(Config.from_dict({"Daily": settings}).to_dict())
                assigned = DailyWorkflow(ctx, account_suffix="917A").local
                self.assertFalse(getattr(assigned, group).is_active)
                self.assertTrue(assigned.tacetSuppressionFSM.is_active)
                unassigned = DailyWorkflow(ctx, account_suffix="9892").local
                self.assertTrue(getattr(unassigned, group).is_active)
                self.assertFalse(unassigned.tacetSuppressionFSM.is_active)
