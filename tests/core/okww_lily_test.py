import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.core.combat.okww.backend import OKWWCombatSystem
from src.core.combat.okww.characters.BaseChar import BaseChar, CharType, Elements
from src.core.combat.okww.characters.Lily import Lily
from src.core.combat.okww.inputs import CombatInput
from src.core.combat.okww.labels import Labels
from src.core.combat.okww.rotation import NotInCombatException
from src.core.combat.okww.runtime import CombatRuntime
from src.core.exceptions import StopError


class LilyTest(unittest.TestCase):
    def test_wwa_name_hint_resolves_to_spectro_healer(self):
        runtime = CombatRuntime(None, None, None, lambda: None)
        runtime.frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        runtime.hints = ("莉莉",)
        character = runtime._identify_slot(0)
        self.assertIsInstance(character, Lily)
        self.assertEqual(character.char_type, CharType.HEALER)
        self.assertEqual(character.ring_index, Elements.SPECTRO)
        self.assertEqual(character.buff_time, 30)

    def test_gold_liberation_icon_still_requires_a_ready_cooldown(self):
        runtime = CombatRuntime(None, None, None, lambda: None)
        runtime.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        char = Lily(runtime, 0)
        char.is_current_char = True
        icon = runtime.get_feature_by_name(Labels.lily_liberation)
        crop = icon.box.crop_frame(runtime.frame)
        crop[:] = icon.mat * (0.69, 0.93, 0.97)
        with patch.object(runtime, "has_cd", return_value=False) as cooldown:
            self.assertFalse(runtime.box_highlighted("liberation"))
            self.assertTrue(char.liberation_available())
            cooldown.return_value = True
            self.assertFalse(char.liberation_available())
            cooldown.return_value = False
            crop[:] = icon.mat * 0.6
            self.assertFalse(char.liberation_available())

    def test_rotation_reserves_concerto_for_liberation_and_tops_up_on_failure(self):
        lily = Lily(SimpleNamespace(use_liberation=True, chars=[None, None]), 0)
        for available, result, expected in (
            (False, False, [call(1)]),
            (True, True, [call(0.8)]),
            (True, False, [call(0.8), call()]),
        ):
            with self.subTest(available=available, result=result), patch.multiple(
                lily, in_slumberland=Mock(return_value=False), click_echo=Mock(),
                build_forte=Mock(), is_mouse_forte_full=Mock(return_value=True), perform_heavy=Mock(),
                liberation_available=Mock(return_value=available), fill_concerto=Mock(),
                click_liberation=Mock(return_value=result), switch_next_char=Mock(),
            ):
                lily.do_perform()
                self.assertEqual(lily.fill_concerto.call_args_list, expected)
                lily.perform_heavy.assert_called_once()
                lily.click_liberation.assert_called_once_with(wait_if_cd_ready=0, click_f=False)
                lily.switch_next_char.assert_called_once()

    def test_slumberland_skips_attacks_and_hands_off(self):
        lily = Lily(SimpleNamespace(chars=[None, None]), 0)
        lily.has_intro = True
        with patch.multiple(
            lily, in_slumberland=Mock(return_value=True), continues_normal_attack=Mock(),
            click_echo=Mock(), click_liberation=Mock(), switch_next_char=Mock(),
        ):
            lily.do_perform()
            lily.continues_normal_attack.assert_not_called()
            lily.click_echo.assert_not_called()
            lily.click_liberation.assert_not_called()
            lily.switch_next_char.assert_called_once()

    def test_heavy_stays_held_through_a_transient_missing_marker(self):
        task = CombatInput()
        task.next_frame = Mock()
        task.check_combat = Mock()
        task.mouse_down = Mock()
        task.mouse_up = Mock()
        lily = Lily(task, 0)
        with (
            patch.object(lily, "heavy_in_progress", side_effect=[True, False, True, False, False, False]),
            patch.object(lily, "sleep") as sleep,
            patch("src.core.combat.okww.inputs.time.monotonic",
                  side_effect=[0, 0.01, 0.02, 0.06, 0.12, 0.18, 0.24, 0.36]),
        ):
            self.assertTrue(lily.perform_heavy())
        self.assertEqual(task.next_frame.call_count, 6)
        task.mouse_down.assert_called_once()
        task.mouse_up.assert_called_once()
        sleep.assert_called_once_with(0.7)

    def test_heavy_releases_mouse_on_timeout_or_cancellation(self):
        task = Mock()
        lily = Lily(task, 0)
        with patch.object(lily, "sleep") as sleep:
            for steps, raises in (([False], False), ([True, False], False), ([True, StopError()], True)):
                with self.subTest(steps=steps):
                    task.reset_mock()
                    task.wait_until.side_effect = steps
                    if raises:
                        with self.assertRaises(StopError):
                            lily.perform_heavy()
                    else:
                        self.assertFalse(lily.perform_heavy())
                    task.mouse_down.assert_called_once()
                    task.mouse_up.assert_called_once()
            sleep.assert_not_called()

    def test_combat_pause_guard_uses_the_active_character_and_clears_on_handoff(self):
        blank = np.zeros((900, 1600, 3), dtype=np.uint8)
        images = Mock(screenshot=Mock(return_value=blank))
        backend = OKWWCombatSystem(Mock(), images, Mock(), None, None, open_world=True)
        runtime = backend._runtime
        runtime._checkpoint = Mock()
        runtime.frame = blank
        dream = blank.copy()
        indicator = runtime.get_feature_by_name(Labels.lily_slumberland)
        indicator.box.crop_frame(dream)[:] = indicator.mat
        images.screenshot.return_value = dream
        lily, ally = Lily(runtime, 0), BaseChar(runtime, 1)
        runtime.chars = [lily, ally]
        lily.is_current_char = True
        runtime._combat_seen = 1

        runtime.check_combat()
        self.assertTrue(backend.combat_check_suspended)
        self.assertFalse(lily.liberation_available())

        lily.is_current_char, ally.is_current_char = False, True
        runtime.next_frame()
        self.assertFalse(backend.combat_check_suspended)
        self.assertEqual(runtime._combat_seen, runtime._last_frame)
        images.screenshot.return_value = blank
        runtime._combat_seen -= 10
        with patch.object(runtime, "in_team", return_value=(True, 1, 2)), self.assertRaises(NotInCombatException):
            runtime.check_combat()
