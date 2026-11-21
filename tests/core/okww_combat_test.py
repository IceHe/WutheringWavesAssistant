import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.core.combat.okww.backend import OKWWCombatSystem
from src.core.combat.okww.characters.BaseChar import BaseChar, Elements, SwitchPriority
from src.core.combat.okww.registry import char_dict
from src.core.combat.okww.rotation import NotInCombatException, con_colors
from src.core.combat.okww.runtime import CombatRuntime
from src.core.combat.okww.vision import template_index

FRAMES = Path(__file__).with_name("okww_frames")


def replay(name="in_combat"):
    data = np.frombuffer((FRAMES / f"{name}.png").read_bytes(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def runtime_for(frame):
    runtime = CombatRuntime(None, None, None, None, lambda: None)
    runtime.frame = frame
    return runtime


class RecognitionTest(unittest.TestCase):
    def test_all_builtin_characters_and_portraits(self):
        runtime = runtime_for(replay())
        classes = {info["cls"]: info for info in char_dict.values()}
        self.assertEqual(len(classes), 57)
        for label in char_dict:
            with self.subTest(label=label):
                self.assertIn(label, template_index())
                self.assertTrue(runtime.get_feature_by_name(label).mat.size)
        for cls, info in classes.items():
            with self.subTest(cls=cls):
                self.assertIsInstance(cls(runtime, 0, char_name=info["canonical_name"]), BaseChar)

    def test_three_party_members_and_skill_hud(self):
        runtime = runtime_for(replay())
        self.assertEqual(runtime.in_team(), (True, 0, 3))
        self.assertTrue(runtime.load_chars())
        self.assertEqual([c.char_name for c in runtime.chars],
                         ["char_iuno", "char_roccia", "char_shorekeeper"])
        self.assertTrue(runtime.box_highlighted("resonance"))
        self.assertTrue(runtime.is_con_full())

    def test_two_members_with_unknown_occupied_slot(self):
        frame = replay()
        runtime = runtime_for(frame)
        marker = runtime.get_box_by_name("char_3_text").scale(2)
        marker.crop_frame(frame)[:] = 0
        portrait = runtime.get_box_by_name("box_char_2").scale(1.12)
        portrait.crop_frame(frame)[:] = 0
        runtime.hints = ("Iuno", "Rover", None)
        self.assertTrue(runtime.load_chars())
        self.assertEqual(runtime.in_team(), (True, 0, 2))
        self.assertEqual([c.char_name for c in runtime.chars], ["char_iuno", "unknown"])

    def test_rover_portrait_and_element_from_screen(self):
        runtime = runtime_for(replay("all_cd_1080p"))
        self.assertTrue(runtime.load_chars())
        rover = runtime.chars[1]
        self.assertEqual(rover.char_name, "char_rover")
        for char in runtime.chars:
            char.is_current_char = char is rover
        colors = con_colors[Elements.HAVOC]
        hue = [sum(colors[c]) // 2 for c in "bgr"]
        runtime.get_con_box().crop_frame(runtime.frame)[:] = hue
        self.assertEqual(runtime.get_known_ring_index(rover), Elements.HAVOC)
        self.assertEqual(rover.display_name, "Rover: Havoc")

    def test_no_team_in_blank_frame(self):
        runtime = runtime_for(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertFalse(runtime.load_chars())
        self.assertEqual(runtime.chars, [])

    def test_cooldown_ocr_is_attributed_to_current_slot(self):
        runtime = runtime_for(replay("all_cd_1080p"))
        self.assertTrue(runtime.load_chars())
        x = runtime.width_of_screen(0.84)
        box = SimpleNamespace(x1=x, x2=x + 30, y1=950, y2=970, text="3.2", score=0.99)
        runtime._ocr = lambda: Mock(query=lambda *args, **kwargs: SimpleNamespace(results=[box]))
        self.assertGreater(runtime.get_cd("resonance", char_index=2), 3)
        self.assertEqual(runtime.get_cd("resonance", char_index=0), 0)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_for(replay())
        self.assertTrue(self.runtime.load_chars())
        self.active, self.sub, self.healer = self.runtime.chars
        self.runtime.is_slot_alive = Mock(return_value=True)

    def test_intro_prefers_unbuffed_healer_at_equal_priority(self):
        with (patch.object(self.sub, "get_switch_priority", return_value=SwitchPriority.LOW),
              patch.object(self.healer, "get_switch_priority", return_value=SwitchPriority.LOW)):
            self.assertIs(self.runtime._choose_switch_target(self.active, True), self.healer)

    def test_downed_slot_is_excluded_and_must_priority_wins(self):
        self.runtime.is_slot_alive.side_effect = lambda index: index != self.healer.index
        with patch.object(self.sub, "get_switch_priority", return_value=SwitchPriority.MUST):
            self.assertIs(self.runtime._choose_switch_target(self.active, True), self.sub)

    def test_switch_failure_does_not_mark_target_current(self):
        runtime = self.runtime
        runtime._choose_switch_target = Mock(return_value=self.sub)
        runtime.update_lib_portrait_icon = Mock()
        runtime.check_combat = Mock()
        runtime.next_frame = Mock()
        runtime.sleep = Mock()
        runtime.send_key = Mock()
        runtime.click = Mock()
        self.sub.wait_switch = Mock(return_value=True)
        self.active.f_break = Mock()
        clock = iter(range(100, 150))
        with patch("src.core.combat.okww.rotation.time.time", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(NotInCombatException, "failed switch"):
                runtime.switch_next_char(self.active, free_intro=True)
        self.assertTrue(self.active.is_current_char)
        self.assertFalse(self.sub.is_current_char)


class CombatLifecycleTest(unittest.TestCase):
    def test_extension_timeout_and_cancel_release_inputs(self):
        control = Mock()
        owner = threading.Event()
        owner.set()
        started = threading.Event()
        backend = OKWWCombatSystem(control, Mock(screenshot=replay), None, None, owner)
        backend.is_async = True
        backend.check_boss_hp = False

        def hold(character):
            character.task.send_key_down("E")
            character.task.mouse_down()
            started.set()
            character.task._wait(5)

        with patch.object(BaseChar, "perform", hold):
            backend.start(2)
            try:
                self.assertTrue(started.wait(2.0))
                worker = backend._thread
                backend.start(0.1)
                self.assertIs(backend._thread, worker)
                worker.join(1)
                self.assertTrue(backend.is_paused)
                control.key_up.assert_called_with("E")
                control.mouse_left_up.assert_called_once()

                control.reset_mock()
                started.clear()
                backend.start(2)
                self.assertTrue(started.wait(2.0))
                owner.clear()
                backend.stop(join=True)
                self.assertTrue(backend.is_paused)
                control.key_up.assert_called_with("E")
                control.mouse_left_up.assert_called_once()
            finally:
                backend.stop(join=True)

    def test_unconfirmed_team_does_not_send_input(self):
        control = Mock()
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        backend = OKWWCombatSystem(control, Mock(screenshot=lambda: blank), None, None, None)
        backend.start(0.2)
        self.assertTrue(backend.is_paused)
        self.assertEqual(control.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
