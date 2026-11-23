import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from src.config.config import Config
from src.core.exceptions import StopError
from src.core.geometry import TextBox
from src.core.i18n import I18nText, I18nTr, Language
from src.core.runtime import RuntimeConfig
from src.core.task import LatchTaskFSM, TaskFSMGroup, TaskStatus
from src.service import daily_workflow, tacet_rewards


class TacetRewardNavigatorTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name("tacet_frames") / "reward_waypoint.png"
        self.crop = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        self.blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.control = Mock()
        self.ctx = SimpleNamespace(
            control_service=self.control,
            tr=I18nTr(Language.ZH),
            window_service=Mock(get_client_wh=Mock(return_value=(1280, 720))),
        )
        self.ui = Mock()
        self.ui.is_set.return_value = True
        self.ui.is_on_homepage.return_value = True
        self.ui.snapshot.return_value = self.ui
        self.ui.sleep.side_effect = self.sleep
        self.ui.camera_reset.return_value = self.ui
        self.ui.pick_up.return_value = self.ui
        self.ui.wait.return_value.until.side_effect = lambda check: check()
        self.ui.search.return_value = False
        self.now = 0.0
        self.enterContext(patch.object(tacet_rewards, "time", SimpleNamespace(monotonic=lambda: self.now)))

    def sleep(self, seconds):
        self.now += seconds
        return self.ui

    def frame_with_waypoint(self, x, y):
        frame = self.blank.copy()
        frame[y:y + 80, x:x + 80] = self.crop
        return frame

    def test_finds_upstream_waypoint_at_edges_and_different_resolutions(self):
        for x, y, scale in ((985, 258, 1), (75, 160, 1.5), (1040, 375, 2)):
            with self.subTest(x=x, y=y, scale=scale):
                frame = self.frame_with_waypoint(x, y)
                scaled = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
                marker = tacet_rewards.RewardWaypointVision(scaled).find_waypoint()
                self.assertIsNotNone(marker)
                self.assertAlmostEqual(marker.x / scale, x + 30, delta=2)
                self.assertAlmostEqual(marker.y / scale, y + 30, delta=2)
        self.assertIsNone(tacet_rewards.RewardWaypointVision(self.blank).find_waypoint())

    def test_turns_from_corner_then_follows_fresh_waypoint_to_prompt(self):
        frames = [self.blank, self.blank, self.frame_with_waypoint(100, 160),
                  self.frame_with_waypoint(600, 180), self.frame_with_waypoint(600, 455), self.blank]
        self.ui.grap.side_effect = frames
        self.ui.search.side_effect = [False] * 5 + [True, True]
        self.assertTrue(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui))
        self.assertEqual(self.control.mock_calls, [call.right(0.18), call.left(0.24), call.up(0.20), call.down(0.08)])
        self.ui.camera_reset.assert_called_once()
        self.ui.pick_up.assert_called_once_with()
        self.assertEqual(self.ui.snapshot.call_args, call())
        self.assertEqual(self.ui.search.call_args, call(self.ctx.tr(tacet_rewards.TACET_CLAIM_BUTTONS)))

    def test_low_waypoint_near_center_uses_short_backward_step(self):
        low_right = self.frame_with_waypoint(705, 516)
        self.ui.grap.side_effect = [low_right, self.blank]
        self.ui.search.side_effect = [False, True, True]

        self.assertTrue(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui))

        self.assertEqual(self.control.mock_calls, [call.down(0.18)])
        self.ui.pick_up.assert_called_once_with()

    def test_oscillating_waypoint_stops_search_before_timeout(self):
        left = self.frame_with_waypoint(450, 320)
        right = self.frame_with_waypoint(750, 320)
        self.ui.grap.side_effect = [left, right, left, right]

        with self.assertLogs(tacet_rewards.__name__, "WARNING") as logs:
            self.assertFalse(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui))

        self.assertIn("kept reversing", logs.output[-1])
        self.assertEqual(self.control.mock_calls, [call.left(0.09), call.right(0.08), call.left(0.08)])
        self.ui.pick_up.assert_not_called()

    def test_stationary_waypoint_stops_search_before_timeout(self):
        self.ui.grap.return_value = self.frame_with_waypoint(705, 516)

        with self.assertLogs(tacet_rewards.__name__, "WARNING") as logs:
            self.assertFalse(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui))

        self.assertIn("did not move", logs.output[-1])
        self.assertEqual(self.control.mock_calls, [call.down(0.18)] * 6)
        self.ui.pick_up.assert_not_called()

    def test_missing_waypoint_times_out_without_interacting(self):
        self.ui.grap.return_value = self.blank
        with self.assertLogs(tacet_rewards.__name__, "WARNING"):
            self.assertFalse(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui, timeout=1.1))
        self.assertEqual(self.control.mock_calls, [call.right(0.18), call.right(0.18)])
        self.ui.pick_up.assert_not_called()

    def test_existing_reward_dialog_requires_no_inputs(self):
        self.ui.grap.return_value = self.blank
        self.ui.is_on_homepage.return_value = False
        self.ui.search.return_value = True
        self.assertTrue(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui))
        self.assertEqual(self.control.mock_calls, [])
        self.ui.pick_up.assert_not_called()
        self.ui.search.assert_called_once_with(self.ctx.tr(tacet_rewards.TACET_CLAIM_BUTTONS))

    def test_cancel_after_ocr_does_not_press_interaction(self):
        self.ui.grap.return_value = self.blank
        self.ui.is_set.side_effect = [True, False]
        self.ui.search.return_value = True
        with self.assertRaises(StopError):
            tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui)
        self.ui.pick_up.assert_not_called()
        self.assertEqual(self.control.mock_calls, [])

    def test_unconfirmed_interaction_returns_failure(self):
        self.ui.grap.return_value = self.blank
        self.ui.search.side_effect = [True, False]
        with self.assertLogs(tacet_rewards.__name__, "WARNING"):
            self.assertFalse(tacet_rewards.open_tacet_reward_dialog(self.ctx, self.ui))
        self.ui.pick_up.assert_called_once_with()
        self.assertEqual(self.control.mock_calls, [])


class TacetRewardWorkflowTest(unittest.TestCase):
    def test_daily_selects_navigation_and_retries_before_claiming(self):
        for waypoint_enabled, retry in ((False, False), (True, False), (True, True)):
            with self.subTest(waypoint_enabled=waypoint_enabled, retry=retry):
                daily_settings = {"TacetSuppressionRewardWaypoint": True} if waypoint_enabled else {}
                snapshot = Config.from_dict({"Daily": daily_settings}).to_dict()
                runtime = SimpleNamespace(cfg=RuntimeConfig(snapshot))
                dungeon_task = LatchTaskFSM(name=I18nText.TacetFieldHeartOfStillness)
                local = SimpleNamespace(tacetSuppressionFSM=TaskFSMGroup(dungeon_task), account_suffix=None,
                                        doubleDropTacetSuppressionFSM=Mock(is_active=False), members=[])
                ctx = SimpleNamespace(tr=I18nTr(Language.ZH), control_service=Mock(), img_service=Mock(),
                                      runtime=runtime)
                tr = ctx.tr
                box = TextBox(400, 120, 500, 140, "test", 1.0)
                texts = {
                    tr(dungeon_task.name).raw: [box],
                    str(tr([I18nText.Challenge, I18nText.Go])): [box],
                    str(tr([I18nText.DefeatTheTdsInTheTacetField])): True,
                    tr(I18nText.TacetFieldChallengeComplete).raw: True,
                }
                ui = Mock()
                for method in (ui.sleep, ui.snapshot, ui.pick_up):
                    method.return_value = ui
                ui.wait.return_value.until.return_value = True
                retry_ui = ui.esc.return_value
                retry_ui.sleep.return_value = retry_ui
                retry_ui.wait.return_value.until.side_effect = lambda check: check()
                ui.search.side_effect = lambda query, *args: texts.get(getattr(query, "raw", str(query)))
                waypoint = Mock(side_effect=[False, True] if retry else [True])
                legacy = Mock(return_value=True)
                combat = Mock()
                cost = daily_workflow.Dungeon.TacetSuppression[dungeon_task.name].waveplate or 60
                with (
                    patch.object(daily_workflow, "UIOp", return_value=ui),
                    patch.object(daily_workflow, "RoiEx"),
                    patch.object(daily_workflow.Slider, "points", return_value=[object()]),
                    patch.object(daily_workflow, "query_waveplate_guidebook", return_value=(cost * 2, 0)),
                    patch.object(daily_workflow, "query_waveplate_claim_rewards", return_value=(cost * 2, 0)),
                    patch.object(daily_workflow, "create_combat_system", return_value=combat),
                    patch.object(daily_workflow, "open_tacet_reward_dialog", waypoint),
                    patch.object(daily_workflow, "object_detection", legacy),
                ):
                    self.assertTrue(daily_workflow.doTacetSuppression(ctx, local))
                attempts = 2 if retry else 1
                self.assertEqual(combat.stop.call_args_list, [call(join=True)] * attempts)
                if waypoint_enabled:
                    self.assertEqual(waypoint.call_args_list, [call(ctx, ui)] * attempts)
                    legacy.assert_not_called()
                    ui.pick_up.assert_not_called()
                else:
                    waypoint.assert_not_called()
                    legacy.assert_called_once_with(ctx, search_reward=True, timeout=25)
                    ui.pick_up.assert_called_once_with(2, 0.2)
                restart = tr(I18nText.Restart)
                double_claim = tr(I18nText.TacetFieldClaimX2)
                self.assertEqual([action.args[0] for action in ui.click_text.call_args_list
                                  if action.args[0] in (restart, double_claim)],
                                 [restart, double_claim] if retry else [double_claim])


class AccountTacetRewardWorkflowTest(unittest.TestCase):
    def run_tacet(self, *, initial, reads, outcome="claimed", account_suffix="6663"):
        local = daily_workflow.TaskLocal(account_suffix)
        dungeon = local.tacetFieldHeartOfStillnessFSM
        for challenge in local.tacetSuppressionFSM.children:
            challenge.set_enabled(challenge is dungeon)
        runtime = SimpleNamespace(cfg=RuntimeConfig(Config()))
        ctx = SimpleNamespace(tr=I18nTr(Language.ZH), control_service=Mock(), img_service=Mock(), runtime=runtime)
        ui = Mock()
        ui.snapshot.return_value = ui
        ui.sleep.return_value = ui
        ui.esc.return_value = ui
        tr = ctx.tr
        box = TextBox(400, 120, 500, 140, "test", 1.0)
        texts = {
            str(tr(dungeon.name)): [box],
            str(tr([I18nText.Challenge, I18nText.Go])): [box],
            str(tr([I18nText.DefeatTheTdsInTheTacetField])): True,
            str(tr(I18nText.TacetFieldChallengeComplete)): True,
            str(tr(tacet_rewards.TACET_CLAIM_BUTTONS)): True,
        }
        state = None

        def click_text(query, *args, **kwargs):
            nonlocal state
            if query == tr(I18nText.TacetFieldClaimX2):
                state = outcome
                return outcome != "missing"
            if query == tr(I18nText.WeeklyCancel):
                state = None
                return True
            if query in (tr(I18nText.TacetFieldExit), tr(I18nText.TacetFieldRestart)):
                if account_suffix is not None and state != "claimed":
                    return False
                state = None
            return True

        def search(query, *args):
            if state == "replenish":
                if query == tr(I18nText.ReplenishWaveplate):
                    return True
            if state == "claimed":
                if query in (tr(I18nText.TacetFieldExit), tr(I18nText.TacetFieldRestart)):
                    return True
            return texts.get(str(query))

        ui.click_text.side_effect = click_text
        ui.search.side_effect = search
        ui.wait.side_effect = lambda *args, **kwargs: Mock(
            until=lambda check: check() if kwargs.get("timeout") == 5 else True)
        combat = Mock()
        with (
            patch.object(daily_workflow, "UIOp", return_value=ui),
            patch.object(daily_workflow, "RoiEx"),
            patch.object(daily_workflow.Slider, "points", return_value=[object()]),
            patch.object(daily_workflow, "query_waveplate_guidebook", return_value=(initial, 0)),
            patch.object(daily_workflow, "query_waveplate_claim_rewards",
                         side_effect=[(waveplates, 0) for waveplates in reads]),
            patch.object(daily_workflow, "create_combat_system", return_value=combat),
        ):
            daily_workflow.doTacetSuppression(ctx, local)
        return dungeon.status, ui, tr, combat

    def test_claims_only_increments_of_120_from_current_waveplates(self):
        for initial, reads in ((119, []), (120, [120]), (180, [180]), (240, [240, 120])):
            with self.subTest(initial=initial):
                status, ui, tr, combat = self.run_tacet(initial=initial, reads=reads)
                self.assertEqual(status, TaskStatus.COMPLETED)
                self.assertEqual(ui.click_text.call_args_list.count(call(tr(I18nText.TacetFieldClaimX2), delay=0.4)),
                                 len(reads))
                self.assertEqual(combat.start.call_count, len(reads))
                self.assertEqual(ui.click_text.call_args_list.count(call(tr(I18nText.TacetFieldRestart), delay=0.3)),
                                 max(0, len(reads) - 1))
                self.assertNotIn(call(tr(I18nText.TacetFieldClaim), delay=0.4), ui.click_text.call_args_list)

    def test_fresh_ocr_blocks_claim_if_waveplates_drop_or_cannot_be_read(self):
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            status, ui, tr, combat = self.run_tacet(initial=None, reads=[])
        self.assertEqual(status, TaskStatus.FAILED)
        combat.start.assert_not_called()
        ui.esc.assert_called_once()

        status, ui, tr, combat = self.run_tacet(initial=120, reads=[119])
        self.assertEqual(status, TaskStatus.COMPLETED)
        combat.start.assert_called_once()
        self.assertNotIn(call(tr(I18nText.TacetFieldClaimX2), delay=0.4), ui.click_text.call_args_list)
        ui.esc.assert_called_once()
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            status, ui, tr, _ = self.run_tacet(initial=120, reads=[None])
        self.assertEqual(status, TaskStatus.FAILED)
        self.assertNotIn(call(tr(I18nText.TacetFieldClaimX2), delay=0.4), ui.click_text.call_args_list)

    def test_unconfirmed_claim_never_retries_or_restarts(self):
        for outcome in ("unconfirmed", "missing", "replenish"):
            with self.subTest(outcome=outcome), self.assertLogs(tacet_rewards.__name__, "WARNING"):
                status, ui, tr, combat = self.run_tacet(initial=240, reads=[240], outcome=outcome)
                self.assertEqual(status, TaskStatus.FAILED)
                self.assertEqual(ui.click_text.call_args_list.count(call(tr(I18nText.TacetFieldClaimX2), delay=0.4)), 1)
                self.assertNotIn(call(tr(I18nText.TacetFieldRestart), delay=0.3), ui.click_text.call_args_list)
                combat.start.assert_called_once()
                if outcome == "replenish":
                    ui.click_text.assert_any_call(tr(I18nText.WeeklyCancel), delay=0.2)

    def test_single_account_can_still_claim_once_with_60(self):
        status, ui, tr, _ = self.run_tacet(initial=60, reads=[60], account_suffix=None)
        self.assertEqual(status, TaskStatus.COMPLETED)
        ui.click_text.assert_any_call(tr(I18nText.TacetFieldClaim), delay=0.4)
