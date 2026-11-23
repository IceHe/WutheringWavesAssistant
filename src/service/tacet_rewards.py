import logging
import time

import numpy as np

from src.core.combat.okww.vision import Box, Vision
from src.core.exceptions import StopError
from src.core.i18n import I18nText
from src.core.pages import UIOp
from src.core.workflow import NodeContext
from src.service.common_workflow import RoiEx

logger = logging.getLogger(__name__)

TACET_CLAIM_BUTTONS = [I18nText.TacetFieldClaim, I18nText.TacetFieldClaimX2]
_OPPOSITE_DIRECTION = {"left": "right", "right": "left", "up": "down", "down": "up"}


class RewardWaypointVision(Vision):
    def __init__(self, frame: np.ndarray):
        self.frame = frame

    def find_waypoint(self) -> Box | None:
        region = self.box_of_screen(0.03, 0.10, 0.97, 0.81, hcenter=True, vcenter=True)
        return self.find_one("treasure_icon", box=region, threshold=0.8, target_height=720)


def _movement_to_waypoint(marker: Box, width: int, height: int, last_direction: str | None) -> tuple[str, float]:
    x = (marker.x + marker.width / 2) / width
    y = (marker.y + marker.height / 2) / height
    # Let low, nearly centered markers guide forward/backward movement before refining aim.
    x_margin = 0.10 if y >= 0.65 else 0.07
    distance = abs(x - 0.5)
    if distance > x_margin:
        direction = "left" if x < 0.5 else "right"
    else:
        y -= 0.05
        y_center = {"up": 0.60, "down": 0.45}.get(last_direction, 0.50)
        direction = "down" if y > y_center else "up"
        distance = abs(y - 0.5)
    return direction, round(max(0.08, min(0.24, distance * 0.8)), 2)


def open_tacet_reward_dialog(ctx: NodeContext, ui: UIOp, timeout: float = 25.0) -> bool:
    """Follow the reward waypoint and leave the full claim dialog in ui's OCR snapshot."""
    logger.info("Searching for the tacet reward using the OKWW waypoint")
    ui.activate()
    deadline = time.monotonic() + timeout
    dialogue = RoiEx(ctx).dialogue
    claim_text = ctx.tr(TACET_CLAIM_BUTTONS)
    prompt_text = ctx.tr(I18nText.ClaimRewards)
    control = ctx.control_service
    last_direction = None
    last_progress = None
    stationary_steps = 0
    reversals = 0
    misses = 0
    while time.monotonic() < deadline:
        if not ui.is_set():
            raise StopError()
        frame = ui.grap()
        if not ui.is_on_homepage(img=frame):
            if ui.snapshot(img=frame).search(claim_text):
                return True
            ui.sleep(0.35)
            continue

        if ui.snapshot(roi=dialogue, img=frame).search(prompt_text):
            if not ui.is_set():
                raise StopError()
            if deadline - time.monotonic() <= 0.25:
                break
            ui.pick_up().sleep(0.25)
            if ui.wait(timeout=min(deadline - time.monotonic(), 3.0)).until(
                    lambda: ui.snapshot().search(claim_text)):
                return True
            logger.warning("Tacet reward interaction did not open the claim dialog")
            return False

        marker = RewardWaypointVision(frame).find_waypoint()
        if not ui.is_set():
            raise StopError()
        if time.monotonic() >= deadline:
            break
        if marker is None:
            misses += 1
            if misses == 1:
                # Allow a transiently hidden marker to reappear before turning.
                ui.sleep(0.2)
            else:
                logger.debug("Reward waypoint outside the view, turning the camera")
                control.right(0.18)
                ui.sleep(0.35).camera_reset().sleep(0.45)
                last_direction = None
                last_progress = None
                stationary_steps = reversals = 0
            continue

        misses = 0
        height, width = frame.shape[:2]
        if (last_progress is not None
                and abs(last_progress.x - marker.x) < width * 0.01
                and abs(last_progress.y - marker.y) < height * 0.01):
            stationary_steps += 1
            if stationary_steps >= 6:
                logger.warning("Tacet reward waypoint did not move after six steps")
                return False
        else:
            last_progress = marker
            stationary_steps = 0

        direction, seconds = _movement_to_waypoint(marker, width, height, last_direction)
        if _OPPOSITE_DIRECTION.get(last_direction) == direction:
            reversals += 1
            if reversals >= 3:
                logger.warning("Tacet reward navigation kept reversing direction")
                return False
            seconds = 0.08
        elif direction != last_direction:
            reversals = 0
        if direction != last_direction:
            logger.debug("Reward waypoint at (%d, %d), moving %s", marker.x, marker.y, direction)
        getattr(control, direction)(seconds)
        last_direction = direction
        ui.sleep(0.10)

    logger.warning("Timed out searching for the tacet reward waypoint")
    return False


def claim_tacet_double_reward(ctx: NodeContext, ui: UIOp) -> bool:
    """Submit one double claim and wait for the result page before allowing a repeat."""
    if not ui.snapshot().click_text(ctx.tr(I18nText.TacetFieldClaimX2), delay=0.4):
        logger.warning("Tacet double claim button was not found")
        return False

    cancel = ctx.tr(I18nText.WeeklyCancel)
    replenish = ctx.tr(I18nText.ReplenishWaveplate)
    reminder = ctx.tr(I18nText.DoNotShowAgain)
    exit_text = ctx.tr(I18nText.TacetFieldExit)
    restart = ctx.tr(I18nText.TacetFieldRestart)

    def read_result():
        ui.snapshot()
        if ui.search(replenish) or (ui.search(cancel) and ui.search(reminder)):
            return "replenish"
        if ui.search(exit_text) and ui.search(restart):
            return "claimed"
        return None

    result = ui.sleep(0.3).wait(timeout=5).until(read_result)
    if result == "claimed":
        return True
    if result == "replenish":
        if not ui.click_text(cancel, delay=0.2):
            ui.esc()
        logger.warning("Tacet double claim requested waveplate replenishment")
    else:
        logger.warning("Tacet double claim result could not be confirmed")
    return False
