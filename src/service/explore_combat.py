"""Passive combat cues and encounter state for the exploration task."""

import time
from dataclasses import dataclass

import cv2

from src.core.combat.combat_core import BaseResonator
from src.core.combat.okww.vision import Vision


@dataclass(frozen=True)
class CombatCues:
    hud_visible: bool
    locked: bool = False
    enemy_bar: bool = False


class ExploreCombatDetector(Vision):
    def _has_combat_hud(self, frame, world_hud):
        if frame is None or not frame.size:
            return False
        self.frame = frame[:, :, :3]
        # Some battle HUDs hide the top-row icons used by is_on_homepage().
        return world_hud or any(
            self.find_one(f"char_{slot}_text", threshold=0.88) for slot in range(1, 4)
        )

    def detects_combat(self, frame, *, world_hud):
        if not self._has_combat_hud(frame, world_hud):
            return False
        return (BaseResonator.is_boss_health_bar_exist(self.frame)
                or self.has_target()
                or self._has_enemy_health_bar())

    def okww_cues(self, frame, *, world_hud, fighting):
        if not self._has_combat_hud(frame, world_hud):
            return CombatCues(False)
        if self.has_target():
            return CombatCues(True, locked=True)
        enemy_bar = (BaseResonator.is_boss_health_bar_exist(self.frame)
                     or self.find_one("boss_break_shield")
                     or self.find_one("boss_break_lock")
                     or self._has_enemy_health_bar(fighting=fighting))
        return CombatCues(True, enemy_bar=bool(enemy_bar))

    def _has_enemy_health_bar(self, *, fighting=False):
        height, width = self.frame.shape[:2]
        # Exclude the minimap, party slots, and the player's skill and HP bars.
        area = self.frame[height // 10:round(height * 0.86), width // 10:round(width * 0.85)]
        # BGR range of the red enemy HP bars used by OKWW's combat detector.
        mask = cv2.inRange(area, (55, 55, 174), (76, 85, 225))
        bars, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # During combat OKWW also checks nearly depleted bars when trying to retarget.
        min_width = max(5, round(height * (12 if fighting else 100) / 2160))
        min_height = max(2, round(height * 9 / 2160))
        min_aspect = 2 if fighting else 5
        for bar in bars:
            _, _, w, h = cv2.boundingRect(bar)
            if (w >= min_width and min_height <= h <= 3 * min_height and w >= min_aspect * h
                    and cv2.contourArea(bar) >= (w - 1) * (h - 1) * 0.75):
                return True
        return False


class Encounter:
    """Confirm entry over two frames and tolerate short HUD interruptions."""

    def __init__(self):
        self.active = False
        self.handled = False
        self._hits = 0
        self._last_seen = None

    def observe(self, signal: bool, now: float):
        if signal:
            if self._last_seen is None or now - self._last_seen > 1:
                self._hits = 0
            self._last_seen = now
            self._hits += 1
            if self._hits >= 2:
                self.active = True
        else:
            self._hits = 0
            if self._last_seen is not None and now - self._last_seen >= 3:
                self.active = False
                self.handled = False
                self._last_seen = None

    def suppress(self, now: float):
        self.handled = True
        self._last_seen = now


class OKWWTargetRecovery:
    """Bound lock confirmation and retries without taking extra screenshots."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._started_at = None
        self._last_bar_at = None
        self._next_attempt = 0.0

    def expired(self, now, *, enemy_bar, fighting):
        if self._started_at is None:
            self._started_at = now
        if fighting and enemy_bar:
            self._last_bar_at = now
        if self._last_bar_at is None:
            return now - self._started_at >= 3
        return now - self._started_at >= 30 or now - self._last_bar_at >= 1.2

    def can_click(self, now):
        if now < self._next_attempt:
            return False
        self._next_attempt = now + 0.5
        return True


class ExploreCombatFrames:
    """Remember the combat worker's most recent full frame for the exploration poller."""

    def __init__(self, images):
        self._images = images
        self._latest = None
        self._last_read = None

    def __getattr__(self, name):
        return getattr(self._images, name)

    def screenshot(self, region=None):
        if region is not None:
            return self._images.screenshot(region)
        frame = self._images.screenshot()
        self._latest = (time.monotonic(), frame)
        return frame

    def frame_for_detection(self):
        latest = self._latest
        if latest is not None and time.monotonic() - latest[0] <= 0.5:
            if latest[0] == self._last_read:
                return None
            self._last_read = latest[0]
            return latest[1]
        return self._images.screenshot()
