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


class RewardWaypointVision(Vision):
    def __init__(self, frame: np.ndarray):
        self.frame = frame

    def find_waypoint(self) -> Box | None:
        region = self.box_of_screen(0.03, 0.10, 0.97, 0.81, hcenter=True, vcenter=True)
        return self.find_one("treasure_icon", box=region, threshold=0.8, target_height=720)


def _direction_to_waypoint(marker: Box, width: int, height: int, last_direction: str | None) -> str:
    x = (marker.x + marker.width / 2) / width
    y = (marker.y + marker.height / 2) / height - 0.05
    # Keep moving on the current axis within a small margin to avoid oscillating.
    x_margin = 0.07 if last_direction in ("up", "down") else 0.04
    if abs(x - 0.5) > x_margin:
        return "left" if x < 0.5 else "right"
    y_center = {"up": 0.60, "down": 0.45}.get(last_direction, 0.50)
    return "down" if y > y_center else "up"


def open_tacet_reward_dialog(ctx: NodeContext, ui: UIOp, timeout: float = 25.0) -> bool:
    """Follow the reward waypoint and leave the full claim dialog in ui's OCR snapshot."""
    logger.info("Searching for the tacet reward using the OKWW waypoint")
    ui.activate()
    deadline = time.monotonic() + timeout
    dialogue = RoiEx(ctx).dialogue
    claim_text = ctx.tr(I18nText.TacetFieldClaim)
    prompt_text = ctx.tr(I18nText.ClaimRewards)
    control = ctx.control_service
    last_direction = None
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
            continue

        misses = 0
        height, width = frame.shape[:2]
        direction = _direction_to_waypoint(marker, width, height, last_direction)
        if direction != last_direction:
            logger.debug("Reward waypoint at (%d, %d), moving %s", marker.x, marker.y, direction)
        getattr(control, direction)(0.24)
        last_direction = direction
        ui.sleep(0.10)

    logger.warning("Timed out searching for the tacet reward waypoint")
    return False
