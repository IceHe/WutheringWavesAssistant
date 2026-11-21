import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np
import win32api
import win32con

from src.config.config import ControlsConfig
from src.core.combat.okww.backend import OKWWCombatSystem
from src.core.combat.okww.characters.BaseChar import BaseChar, Elements, SwitchPriority
from src.core.combat.okww.post_message import PostMessageCombatInput
from src.core.combat.okww.registry import char_dict
from src.core.combat.okww.rotation import NotInCombatException, con_colors
from src.core.combat.okww.runtime import CombatRuntime
from src.core.combat.okww.vision import template_index
from src.core.exceptions import StopError
from src.core.runtime import ControlsRuntimeConfig

FRAMES = Path(__file__).with_name("okww_frames")


def replay(name="in_combat"):
    data = np.frombuffer((FRAMES / f"{name}.png").read_bytes(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def runtime_for(frame):
    runtime = CombatRuntime(None, None, None, lambda: None)
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


class PostMessageInputTest(unittest.TestCase):
    def setUp(self):
        self.window = SimpleNamespace(window=42)
        controls = ControlsRuntimeConfig(ControlsConfig(teamMember2="K"))
        self.inputs = PostMessageCombatInput(self.window, controls)
        post = patch("src.core.combat.okww.post_message.win32gui.PostMessage")
        self.post = post.start()
        self.addCleanup(post.stop)

    def test_mapped_switch_key_has_scan_code_and_matching_release(self):
        self.inputs.key_down("2")
        self.window.window = 43
        self.inputs.key_up("2")
        vk = ord("K")
        params = (win32api.MapVirtualKey(vk, 0) << 16) | 1
        self.assertEqual(self.post.call_args_list, [
            call(42, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0),
            call(42, win32con.WM_KEYDOWN, vk, params),
            call(42, win32con.WM_KEYUP, vk, params | (3 << 30)),
        ])

    def test_click_moves_then_waits_before_pressing_and_releases_on_stop(self):
        sequence = Mock()
        sequence.attach_mock(self.post, "post")
        wait = Mock(side_effect=[None, StopError()])
        sequence.attach_mock(wait, "wait")
        pos = win32api.MAKELONG(640, 360)
        with self.assertRaises(StopError):
            self.inputs.click("middle", 0.02, wait, position=(640, 360))
        self.assertEqual(sequence.mock_calls, [
            call.post(42, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0),
            call.post(42, win32con.WM_MOUSEMOVE, 0, pos),
            call.wait(0.02),
            call.post(42, win32con.WM_MBUTTONDOWN, win32con.MK_MBUTTON, pos),
            call.wait(0.02),
            call.post(42, win32con.WM_MBUTTONUP, 0, pos),
        ])

    def test_stop_during_click_lead_in_skips_button_down(self):
        with self.assertRaises(StopError):
            self.inputs.click("left", 0.02, Mock(side_effect=StopError()))
        self.assertEqual([args[1] for args, _ in self.post.call_args_list],
                         [win32con.WM_ACTIVATE, win32con.WM_MOUSEMOVE])

    def test_heavy_attack_keeps_original_coordinates_even_after_another_click(self):
        self.inputs.click("middle", 0.01, Mock(), position=(640, 360))
        self.post.reset_mock()
        self.inputs.mouse_down("left")
        self.inputs.click("middle", 0.01, Mock(), position=(100, 200))
        self.window.window = 43
        self.inputs.release_inputs()
        pos = win32api.MAKELONG(640, 360)
        self.post.assert_any_call(42, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, pos)
        self.post.assert_called_with(42, win32con.WM_LBUTTONUP, 0, pos)


class CombatLifecycleTest(unittest.TestCase):
    @patch("src.core.combat.okww.post_message.win32gui.PostMessage")
    def test_extension_timeout_and_cancel_release_inputs(self, post):
        control = Mock()
        owner = threading.Event()
        owner.set()
        started = threading.Event()
        backend = OKWWCombatSystem(control, Mock(screenshot=replay), SimpleNamespace(window=42), None, owner)
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
                params = (win32api.MapVirtualKey(ord("E"), 0) << 16) | 1 | (3 << 30)
                post.assert_any_call(42, win32con.WM_KEYUP, ord("E"), params)
                post.assert_any_call(42, win32con.WM_LBUTTONUP, 0, 0)
                post.assert_any_call(42, win32con.WM_MBUTTONUP, 0, 0)

                post.reset_mock()
                started.clear()
                backend.start(2)
                self.assertTrue(started.wait(2.0))
                owner.clear()
                backend.stop(join=True)
                self.assertTrue(backend.is_paused)
                post.assert_any_call(42, win32con.WM_KEYUP, ord("E"), params)
                post.assert_any_call(42, win32con.WM_LBUTTONUP, 0, 0)
                self.assertEqual(control.mock_calls, [])
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
