import unittest
from unittest.mock import Mock, patch

from src.core.combat.okww.characters.Mornye import Mornye


class MornyeRotationTest(unittest.TestCase):
    def setUp(self):
        self.task = Mock()
        self.role = Mornye(self.task, 2, char_name="char_moning")

    def test_echo_cooldown_clears_knockback_evidence(self):
        self.task.box_highlighted.return_value = True
        self.task.has_cd.return_value = False
        self.assertFalse(self.role.detect_elbow_strike())

        self.task.box_highlighted.return_value = False
        self.task.has_cd.return_value = True
        self.assertFalse(self.role.detect_elbow_strike())

        self.task.has_cd.return_value = False
        self.assertFalse(self.role.detect_elbow_strike())

    def test_echo_icon_dimming_without_cooldown_still_signals_knockback(self):
        self.task.box_highlighted.side_effect = [True, False]
        self.task.has_cd.return_value = False
        self.assertFalse(self.role.detect_elbow_strike())
        self.assertTrue(self.role.detect_elbow_strike())

    def test_ground_and_air_share_the_same_deadline(self):
        self.role.on_air = Mock(side_effect=[False, True])
        self.role.combo_limit = Mock(return_value=False)
        self.role.not_on_air_actions = Mock()
        self.role.on_air_actions = Mock()
        self.role.switch_next_char = Mock()
        with patch("src.core.combat.okww.characters.Mornye.time.monotonic", side_effect=[100, 108, 109]):
            self.role.do_perform()
        self.role.not_on_air_actions.assert_called_once_with(110)
        self.role.on_air_actions.assert_called_once_with(110)
        self.role.switch_next_char.assert_called_once()

    def test_expired_ground_phase_switches_without_starting_aerial_phase(self):
        self.role.on_air = Mock(return_value=False)
        self.role.combo_limit = Mock(return_value=False)
        self.role.not_on_air_actions = Mock()
        self.role.on_air_actions = Mock()
        self.role.switch_next_char = Mock()
        with patch("src.core.combat.okww.characters.Mornye.time.monotonic", side_effect=[100, 111, 111]):
            self.role.do_perform()
        self.role.on_air_actions.assert_not_called()
        self.role.switch_next_char.assert_called_once()
