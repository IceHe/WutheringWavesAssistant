"""The task interface used by the built-in OKWW characters."""

import logging
import re
import time

from src.core.combat.combat_core import BaseResonator
from src.core.i18n import I18N_TEXT, Language

from .characters.BaseChar import BaseChar
from .inputs import CombatInput
from .registry import char_dict
from .rotation import NotInCombatException, Rotation
from .vision import Vision

logger = logging.getLogger(__name__)

_CHAR_NUMBER_TEMPLATES = tuple(f"char_{i}_text" for i in range(1, 4))
_ROVER_NAMES = {"rover", "漂泊者"}
_WWA_NAMES = {
    "Douling": "Buling", "Xigelika": "Sigrika", "Linnai": "Lynae",
    "Luhesi": "LuukHerssen", "Zani": "Zanni", "YangYangSp": "YangyangXuanling",
}


def _name_key(name):
    return re.sub(r"[\W_]", "", name.casefold())


def _build_aliases():
    aliases = {}
    translations = {key.casefold(): value for key, value in I18N_TEXT.items()}
    for label, info in char_dict.items():
        class_name = info["cls"].__name__
        for name in (label, class_name):
            aliases[_name_key(name)] = info
        key = _WWA_NAMES.get(class_name, class_name)
        for language in (Language.ZH, Language.EN):
            if text := translations.get(key.casefold(), {}).get(language):
                aliases[_name_key(text.raw)] = info
    return aliases


_ALIASES = _build_aliases()


class CombatRuntime(Rotation, Vision, CombatInput):
    debug = False
    name = "WWA OKWW Combat"

    def __init__(self, control, images, window, ocr, checkpoint, *, open_world=False):
        self.control = control
        self.images = images
        self.window = window
        self._ocr = ocr
        self._checkpoint = checkpoint
        self._open_world = open_world
        self.hints = ()
        self._last_frame = 0.0
        self._last_input = 0.0
        self._last_pickup = 0.0
        self._pressed_keys = set()
        self._pressed_buttons = set()
        self.auto_pickup = False
        self.check_boss_hp = True
        self.logger = logger
        # WWA's control service maps logical keys to the in-game bindings.
        self.key_config = {"Resonance Key": "E", "Echo Key": "Q", "Liberation Key": "R", "Tool Key": "T"}
        self.char_config = {"Iuno C6": False, "Roccia C6": False, "Chisa DPS": False, "Suoming DPS": False}
        self.con_full_size = {}
        self.reset_session()

    def reset_session(self):
        self.chars = []
        self.frame = None
        self._team = None
        self._pending_team_size = None
        self._last_team = 0.0
        self.freeze_durations = []
        self.cds = {}
        self.combat_start = 0.0
        self.in_liberation = False
        self.skip_combat_check = False
        self.has_lavitator = False
        self.use_liberation = True
        self._last_break_check = 0.0
        self._break_ready = False
        self._combat_seen = 0.0

    def next_frame(self):
        self._checkpoint()
        delay = 0.025 - (time.monotonic() - self._last_frame)
        if delay > 0:
            self._wait(delay)
        frame = self.images.screenshot()
        self._checkpoint()
        if frame is None or frame.ndim != 3 or not frame.size:
            raise NotInCombatException("Invalid combat frame")
        self.frame = frame[:, :, :3]
        self._last_frame = time.monotonic()
        self._team = None
        return self.frame

    def in_team(self):
        if self._team is None:
            markers = [i for i, label in enumerate(_CHAR_NUMBER_TEMPLATES) if self.find_one(label)]
            count = len(markers) + 1
            if markers and max(markers) < count:
                idx = next(i for i in range(count) if i not in markers)
                self._team = True, idx, count
            elif not markers and self._solo_hud():
                self._team = True, 0, 1
            else:
                self._team = False, -1, 0
            if self._team[0]:
                self._last_team = time.monotonic()
        return self._team

    def _solo_hud(self):
        # The markers also disappear in menus and during Liberation.
        if len(self.chars) > 1 or sum(bool(hint) for hint in self.hints) > 1 or self.in_liberation:
            return False
        return bool(self.has_target() and self.box_highlighted("resonance"))

    def in_team_and_world(self):
        return self.in_team()[0]

    def wait_in_team_and_world(self, time_out=10, raise_if_not_found=True):
        return self.wait_until(self.in_team_and_world, time_out=time_out, raise_if_not_found=raise_if_not_found)

    def load_chars(self):
        in_team, active, count = self.in_team()
        if not in_team:
            return False
        if self.chars and len(self.chars) != count:
            # A transiently hidden marker shouldn't rebuild the team mid-animation.
            if self._pending_team_size != count:
                self._pending_team_size = count
                self._team_size_since = time.monotonic()
            if time.monotonic() - self._team_size_since < 0.5:
                return False
            self.cds.clear()
            self.freeze_durations.clear()
            self.chars.clear()
        self._pending_team_size = None
        if not self.chars:
            self.combat_start = time.time()
            self.has_lavitator = bool(self.find_one("edge_levitator", threshold=0.8))
            self.chars = [self._identify_slot(i) for i in range(count)]
            self.chars[active].last_switch_in_time = self.combat_start
            logger.info("OKWW team: %s", ", ".join(str(char) for char in self.chars))
        for i, char in enumerate(self.chars):
            char.is_current_char = i == active
        return True

    def _identify_slot(self, index):
        box = self.get_box_by_name(f"box_char_{index + 1}").scale(1.12)
        portrait = self.find_best_match_in_box(box, char_dict, threshold=0.6)
        if portrait:
            info = char_dict[portrait.name]
            confidence = portrait.confidence
        else:
            hint = self.hints[index] if index < len(self.hints) else None
            name = _name_key(hint) if hint else ""
            # Legacy WWA OCR supplies Rover when it cannot read a name.
            info = _ALIASES.get(name) if name not in _ROVER_NAMES else None
            confidence = 0.5
        if info is None:
            logger.info("OKWW slot %d could not be identified, using generic combat", index + 1)
            return BaseChar(self, index, char_name="unknown", confidence=0)
        char = info["cls"](self, index, char_name=info["canonical_name"], confidence=confidence,
                           ring_index=info.get("ring_index", -1), char_type=info["char_type"],
                           buff_time=info["buff_time"])
        char.target_box_short_combat_check = info.get("target_box_short_combat_check", False)
        return char

    def is_slot_alive(self, index):
        return not BaseResonator.is_avatar_grey(self.frame, index + 1)

    def get_known_ring_index(self, char):
        if char.is_current_char:
            return self._ensure_ring_index()
        return char.ring_index if char.ring_index >= 0 else getattr(char, "_last_known_form", -1)

    def is_open_world_auto_combat(self):
        return self._open_world

    def get_target_names(self):
        suffix = "_169" if self.screen_width == 1600 else ""
        return "has_target" + suffix, "no_target" + suffix

    def has_target(self):
        target, no_target = self.get_target_names()
        regions = (self.get_box_by_name(target).scale(1.2), "box_target_enemy_long",
                   "target_box_long2", "target_box_short")
        for region in regions:
            if match := self.find_best_match_in_box(region, (target, no_target), threshold=0.6):
                return match.name == target
        return False

    def check_combat(self, fresh=True):
        self._checkpoint()
        if self.skip_combat_check or self.in_liberation:
            return
        if fresh:
            self.next_frame()
        now = time.monotonic()
        if not self.in_team()[0] and self._last_team and now - self._last_team > 1.5:
            self.raise_not_in_combat("Combat party HUD disappeared")
        if self.check_boss_hp:
            if BaseResonator.is_boss_health_bar_exist(self.frame) or self.has_target():
                self._combat_seen = now
            elif self._combat_seen and now - self._combat_seen > 1.5:
                self.raise_not_in_combat("Boss health and target HUD disappeared")

    @staticmethod
    def raise_not_in_combat(message):
        raise NotInCombatException(message)

    def check_f_break(self):
        if self._break_ready:
            return True
        now = time.monotonic()
        if self.in_liberation or now - self._last_break_check < 0.8:
            return False
        self._last_break_check = now
        self._break_ready = bool(self.find_one("f_break_full", threshold=0.92))
        if not self._break_ready and self.find_one("f_break", box=self.box_of_screen(0.2, 0.2, 0.75, 0.8),
                                                   target_height=720, threshold=0.85):
            self._break_ready = not bool(self.find_one("pick_up_f_hcenter_vcenter", threshold=0.8))
        return self._break_ready

    def f_break(self):
        if not self.check_f_break():
            return False
        deadline = time.monotonic() + 5
        while self._break_ready and time.monotonic() < deadline:
            self.send_key("F", after_sleep=0.1)
            self.click(after_sleep=0.1)
            self._break_ready = False
            self._last_break_check = 0
            self.next_frame()
            self.check_f_break()
        return True

    @staticmethod
    def log_debug(message):
        logger.debug(message)

    @staticmethod
    def log_error(message, notify=False):
        logger.error(message)

    @staticmethod
    def info_set(key, value):
        logger.info("OKWW %s: %s", key, value)

    @staticmethod
    def draw_boxes(name, boxes, **kwargs):
        logger.debug("%s: %s", name, boxes)

    @staticmethod
    def screenshot(name):
        logger.debug("OKWW combat diagnostic: %s", name)
