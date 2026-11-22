import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.config.config import Config, ExploreConfig
from src.core.combat.combat_system import CombatSystem
from src.core.runtime import ExploreRuntimeConfig
from src.core.workflow import TaskSpec
from src.service.explore_combat import (
    CombatCues, Encounter, ExploreCombatDetector, ExploreCombatFrames, OKWWTargetRecovery,
)
from src.service.explore_workflow import ExploreWorkflow
from tests.core.okww_combat_test import replay


class ExploreCombatDetectionTest(unittest.TestCase):
    def test_locked_enemy_and_party_hud_in_replay(self):
        frame = replay()
        detector = ExploreCombatDetector()
        detector.frame = frame
        # The trimmed replay excludes the lock-on icon. Restore its HUD region.
        feature = detector.get_feature_by_name("has_target_169")
        feature.box.crop_frame(frame)[:] = feature.mat
        self.assertTrue(detector.detects_combat(frame, world_hud=False))

    def test_unlocked_enemy_bar_requires_gameplay_hud(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        # A red enemy bar in the world, with no lock-on graphic.
        frame[300:304, 600:650] = (65, 73, 210)
        detector = ExploreCombatDetector()
        self.assertTrue(detector.detects_combat(frame, world_hud=True))
        self.assertFalse(detector.detects_combat(frame, world_hud=False))
        frame[300:340, 600:650] = (65, 73, 210)
        self.assertFalse(detector.detects_combat(frame, world_hud=True))

    def test_option_defaults_to_hotkey_only_and_requires_parent(self):
        self.assertFalse(ExploreRuntimeConfig(ExploreConfig(autoCombat=True)).autoCombatOnEngage)
        self.assertFalse(ExploreRuntimeConfig(ExploreConfig(autoCombatOnEngage=True)).autoCombatOnEngage)
        enabled = ExploreConfig(autoCombat=True, autoCombatOnEngage=True)
        self.assertTrue(ExploreRuntimeConfig(enabled).autoCombatOnEngage)

    def test_okww_accepts_a_short_hp_bar_only_during_combat(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[300:304, 600:612] = (65, 73, 210)
        detector = ExploreCombatDetector()
        self.assertEqual(detector.okww_cues(frame, world_hud=True, fighting=False), CombatCues(True))
        self.assertEqual(detector.okww_cues(frame, world_hud=True, fighting=True),
                         CombatCues(True, enemy_bar=True))

    def test_retarget_timeout_is_bounded_even_when_the_bar_stays_visible(self):
        recovery = OKWWTargetRecovery()
        self.assertFalse(recovery.expired(10, enemy_bar=True, fighting=True))
        self.assertFalse(recovery.expired(39.9, enemy_bar=True, fighting=True))
        self.assertTrue(recovery.expired(40, enemy_bar=True, fighting=True))

    def test_detector_reuses_fresh_worker_frame_once_and_falls_back_if_stale(self):
        first, second = object(), object()
        images = Mock(screenshot=Mock(side_effect=[first, second]))
        frames = ExploreCombatFrames(images)
        with patch("src.service.explore_combat.time.monotonic", side_effect=[10, 10.2, 10.3, 10.6]):
            self.assertIs(frames.screenshot(), first)
            self.assertIs(frames.frame_for_detection(), first)
            self.assertIsNone(frames.frame_for_detection())
            self.assertIs(frames.frame_for_detection(), second)
        self.assertEqual(images.screenshot.call_count, 2)

    def test_state_requires_two_hits_and_rearms_after_clear(self):
        encounter = Encounter()
        encounter.observe(True, 1.0)
        self.assertFalse(encounter.active)
        encounter.observe(True, 1.3)
        self.assertTrue(encounter.active)
        encounter.suppress(1.4)
        encounter.observe(True, 2.0)
        encounter.observe(False, 4.9)
        self.assertTrue(encounter.active)
        self.assertTrue(encounter.handled)
        encounter.observe(False, 5.0)
        self.assertFalse(encounter.active)
        self.assertFalse(encounter.handled)


class ExploreCombatWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.cfg = ExploreRuntimeConfig(ExploreConfig(autoCombat=True, autoCombatOnEngage=True))
        ctx = SimpleNamespace(
            spec=TaskSpec(),
            tr=Mock(return_value=None), control_service=Mock(), img_service=Mock(),
            window_service=Mock(is_foreground_window=Mock(return_value=True)),
            runtime=SimpleNamespace(stop_event=Mock(), cfg=SimpleNamespace(explore=self.cfg)),
        )
        with (patch("src.service.explore_workflow.UIOp"),
              patch.object(ExploreWorkflow, "_ExploreWorkflow__async_init")):
            self.workflow = ExploreWorkflow(ctx)
        self.addCleanup(self.workflow.executor.shutdown)
        self.detector = Mock()
        self.workflow._combat_detector = self.detector
        self.backend = Mock()
        self.target_input = Mock()
        for name, kwargs in (
            ("create_combat_system", {"return_value": self.backend}),
            ("PostMessageCombatInput", {"return_value": self.target_input}),
            ("TeamMember.get_size", {"return_value": 2}),
            ("TeamMember.get_members_by_icon", {"return_value": [None, None, None]}),
        ):
            patcher = patch(f"src.service.explore_workflow.{name}", **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def poll(self, signal, at):
        self.detector.detects_combat.return_value = signal
        with patch("src.service.explore_workflow.time.monotonic", return_value=at):
            self.workflow._poll_combat(Mock())

    def poll_okww(self, cues, at):
        self.detector.okww_cues.return_value = cues
        with patch("src.service.explore_workflow.time.monotonic", return_value=at):
            self.workflow._poll_okww_combat(Mock())

    def test_okww_combat_waits_for_lock_and_does_not_reset_the_camera_again(self):
        bar = CombatCues(True, enemy_bar=True)
        self.poll_okww(bar, 10.0)
        self.target_input.click.assert_not_called()
        self.poll_okww(bar, 10.25)
        self.poll_okww(bar, 10.5)
        self.target_input.click.assert_called_once_with("middle", 0.02, self.workflow.ui.sleep)
        self.workflow.ctx.control_service.camera_reset.assert_not_called()
        self.backend.start.assert_not_called()
        self.poll_okww(CombatCues(True, locked=True), 10.75)
        self.backend.start.assert_called_once_with(600, reset_camera=False)
        self.assertIsInstance(self.workflow._combat_frames, ExploreCombatFrames)

    def test_okww_exits_after_losing_bars_and_ignores_hidden_hud_during_animation(self):
        locked = CombatCues(True, locked=True)
        self.poll_okww(locked, 10.0)
        self.poll_okww(locked, 10.25)
        self.backend.combat_check_suspended = True
        self.poll_okww(CombatCues(False), 16.0)
        self.backend.stop.assert_not_called()
        self.target_input.click.assert_not_called()

        self.backend.combat_check_suspended = False
        self.poll_okww(CombatCues(True, enemy_bar=True), 16.25)
        self.target_input.click.assert_called_once()
        self.poll_okww(CombatCues(True), 17.0)
        self.backend.stop.assert_not_called()
        self.poll_okww(CombatCues(True), 17.5)
        self.backend.stop.assert_called_once_with(join=True)
        self.assertIsNone(self.workflow._combat_frames)

        # A new wave can reappear before the old encounter would have cleared.
        self.poll_okww(locked, 17.6)
        self.backend.start.assert_called_once()
        self.assertTrue(self.detector.okww_cues.call_args.kwargs["fighting"])
        self.poll_okww(locked, 17.7)
        self.assertEqual(self.backend.start.call_count, 2)
        self.assertEqual(self.workflow._okww_recheck_until, 0.0)

    def test_okww_worker_exit_rearms_after_two_fresh_frames(self):
        locked = CombatCues(True, locked=True)
        self.poll_okww(locked, 10.0)
        self.poll_okww(locked, 10.25)
        self.backend.start.assert_called_once()

        self.backend.is_paused = True
        self.poll_okww(locked, 12.0)
        self.assertIsNone(self.workflow.combat_system)
        self.assertEqual(self.workflow._okww_recheck_until, 15.0)
        self.backend.stop.assert_called_once_with(join=True)
        self.backend.is_paused = False
        self.poll_okww(locked, 12.1)
        self.backend.start.assert_called_once()
        self.poll_okww(locked, 12.2)
        self.assertEqual(self.backend.start.call_count, 2)

    def test_escape_cancels_okww_recheck_while_worker_is_idle(self):
        locked = CombatCues(True, locked=True)
        self.poll_okww(locked, 10.0)
        self.poll_okww(locked, 10.25)
        self.backend.is_paused = True
        self.poll_okww(CombatCues(False), 12.0)

        with patch("src.service.explore_workflow.time.monotonic", return_value=12.1):
            self.workflow._on_press(None, True)
        self.assertEqual(self.workflow._okww_recheck_until, 0.0)
        self.poll_okww(locked, 12.2)
        self.poll_okww(locked, 12.3)
        self.backend.start.assert_called_once()
        self.target_input.click.assert_not_called()

    def test_okww_failed_initial_lock_is_suppressed_for_current_encounter(self):
        bar = CombatCues(True, enemy_bar=True)
        self.poll_okww(bar, 10.0)
        self.poll_okww(bar, 10.25)
        self.poll_okww(bar, 13.5)
        self.poll_okww(CombatCues(True, locked=True), 13.75)
        self.backend.start.assert_not_called()
        self.poll_okww(CombatCues(True), 17.0)
        self.poll_okww(CombatCues(True, locked=True), 17.5)
        self.poll_okww(CombatCues(True, locked=True), 17.75)
        self.backend.start.assert_called_once()

    def test_detection_follows_task_backend_and_keeps_wwa_poll_limiter(self):
        cases = (
            ({}, True, "wwa"),
            ({"CombatBackend": "WWA"}, True, "wwa"),
            ({"CombatBackend": "OKWW"}, False, "okww"),
        )
        for game, legacy_flag, expected in cases:
            with self.subTest(game=game):
                config = Config.from_dict({"Game": game, "Explore": {
                    "AutoCombat": True, "AutoCombatOnEngage": True, "AutoCombatOkwwDetection": legacy_flag,
                }})
                self.workflow.ctx.spec = TaskSpec(user_config=config.to_dict())
                cfg = ExploreRuntimeConfig(config.explore)
                self.workflow.ui.is_set.side_effect = [True, True, True, True, False]
                self.workflow.ui.grap.reset_mock()
                self.workflow._combat_limiter = Mock(side_effect=[0.0, 0.15, 0.05, 0.0])
                with (patch.object(self.workflow, "_poll_okww_combat") as okww,
                      patch.object(self.workflow, "_poll_combat") as wwa):
                    self.workflow._run_explore(cfg)
                self.assertEqual(self.workflow.ui.grap.call_count, 2)
                self.assertEqual(wwa.call_count, 2 if expected == "wwa" else 0)
                self.assertEqual(okww.call_count, 2 if expected == "okww" else 0)

    def test_only_okww_polls_each_idle_pass_until_the_window_expires(self):
        for name, expected in (("OKWW", [10.0, 10.1, 10.2, 13.0, 13.25]),
                               ("WWA", [10.0, 13.0, 13.25])):
            with self.subTest(backend=name):
                self.workflow.ctx.spec = TaskSpec(user_config={"Game": {"CombatBackend": name}})
                self.workflow._okww_recheck_until = 13.0
                self.workflow._combat_limiter.next_time = None
                passes = iter([10.0, 10.1, 10.2, 13.0, 13.1, 13.25])
                reads = []
                now = None

                def running():
                    nonlocal now
                    now = next(passes, None)
                    return now is not None

                self.workflow.ui.is_set.side_effect = running
                self.workflow.ui.grap.side_effect = lambda: reads.append(now) or Mock()
                with (patch("src.service.explore_workflow.time.monotonic", side_effect=lambda: now),
                      patch.object(self.workflow, "_poll_okww_combat"),
                      patch.object(self.workflow, "_poll_combat")):
                    self.workflow._run_explore(self.cfg)
                self.assertEqual(reads, expected)

    def test_automatic_only_enters_polling_loop(self):
        self.assertFalse(self.cfg.autoPickup)
        with (patch("src.service.explore_workflow.KeyListener"),
              patch.object(self.workflow, "_run_explore") as loop):
            self.workflow.execute()
        loop.assert_called_once_with(self.cfg)

    def test_okww_selection_keeps_hotkey_only_mode_when_auto_start_is_off(self):
        config = Config.from_dict({"Game": {"CombatBackend": "OKWW"}, "Explore": {"AutoCombat": True}})
        self.workflow.ctx.spec = TaskSpec(user_config=config.to_dict())
        self.workflow.ctx.runtime.cfg.explore = ExploreRuntimeConfig(config.explore)
        with (patch("src.service.explore_workflow.KeyListener") as listener,
              patch.object(self.workflow, "_run_explore") as loop):
            self.workflow.execute()
        listener.return_value.join.assert_called_once_with()
        loop.assert_not_called()

    def test_hotkey_suppresses_current_engagement(self):
        self.poll(True, 10.0)
        self.poll(True, 10.3)
        self.backend.start.assert_called_once_with(600, reset_camera=True)
        self.assertFalse(self.backend.auto_pickup)
        with patch("src.service.explore_workflow.time.monotonic", return_value=10.4):
            self.workflow._on_click(None, True, "5")
        self.backend.stop.assert_called_once_with(join=True)
        self.poll(True, 11.0)
        self.poll(True, 11.3)
        self.backend.start.assert_called_once()
        self.poll(False, 14.4)
        self.poll(True, 16.0)
        self.poll(True, 16.3)
        self.assertEqual(self.backend.start.call_count, 2)

    def test_auto_stops_when_cues_clear_and_manual_start_still_works(self):
        self.poll(True, 10.0)
        self.poll(True, 10.3)
        self.poll(False, 12.0)
        self.backend.stop.assert_not_called()
        self.poll(False, 13.4)
        self.backend.stop.assert_called_once()
        self.workflow._on_click(None, True, "5")
        self.backend.start.assert_called_with(600, reset_camera=True)
        self.assertTrue(self.backend.auto_pickup)
        self.poll(False, 20.0)
        self.assertIs(self.workflow.combat_system, self.backend)
        self.workflow._on_press(None, True)
        self.assertIsNone(self.workflow.combat_system)

    def test_wwa_stop_retires_paused_worker(self):
        backend = CombatSystem(Mock(), Mock())
        backend.is_async = True
        backend.set_resonators(["None"], is_print=False)
        backend.start(10, reset_camera=False)
        worker = backend._thread
        try:
            backend.pause()
        finally:
            backend.stop(join=True)
        self.assertFalse(worker.is_alive())
        backend.control_service.camera_reset.assert_not_called()
