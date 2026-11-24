import time

from ..labels import Labels
from .BaseChar import BaseChar

_LIBERATION_GOLD = {"r": (230, 255), "g": (215, 255), "b": (150, 205)}


class Lily(BaseChar):
    """Finish the heavy channel and build Concerto before entering Slumberland."""

    FORTE_TIMEOUT = 10
    HEAVY_START_TIMEOUT = 2
    HEAVY_TIMEOUT = 6
    CONCERTO_TIMEOUT = 3

    def skip_combat_check(self):
        return self.in_slumberland()

    def do_perform(self):
        if not self.in_slumberland():
            if self.has_intro:
                self.continues_normal_attack(0.8)
            self.click_echo(time_out=0)
            self.build_forte()
            if self.is_mouse_forte_full() or self.is_forte_full():
                self.perform_heavy()
            can_liberate = self.task.use_liberation and self.liberation_available()
            self.fill_concerto(0.8 if can_liberate else 1)
            # Slumberland's Liberation button ends the state. Keep it active for the handoff.
            liberated = self.click_liberation(wait_if_cd_ready=0, click_f=False)
            if can_liberate and not liberated and not self.in_slumberland():
                self.fill_concerto()
        if len(self.task.chars) == 1 and self.in_slumberland():
            self.sleep(0.15)
            return
        self.switch_next_char()

    def fill_concerto(self, target=1):
        # Liberation supplies the final 20% when it is available.
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.CONCERTO_TIMEOUT:
            self.cycle_start()
            if self.get_current_con() >= target:
                return True
            if not self.click_resonance(send_click=False, time_out=0.8)[0]:
                self.click(interval=0.1)
            self.cycle_sleep()
        self.logger.warning("Lily Concerto top-up timed out: target=%s", target)
        return False

    def build_forte(self):
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.FORTE_TIMEOUT:
            self.cycle_start()
            if self.is_mouse_forte_full() or self.is_forte_full():
                return True
            if not self.click_resonance(send_click=False, time_out=0.8)[0]:
                self.click(interval=0.1)
            self.cycle_sleep()
        self.logger.warning("Lily Dream Perfume did not fill before timeout")
        return False

    def heavy_in_progress(self):
        # Ready and channeling share the inner flower. The full-gauge prompt disappears on consumption.
        return (not self.is_mouse_forte_full()
                and bool(self.task.find_one(Labels.lily_heavy, threshold=0.8)))

    def in_slumberland(self):
        return bool(self.task.find_one(Labels.lily_slumberland, threshold=0.8))

    def liberation_available(self, check_color=True):
        if self.in_slumberland() or not (icon := self.task.find_one(Labels.lily_liberation, threshold=0.8)):
            return False
        if (check_color and self.is_current_char and not self.task.box_highlighted("liberation")
                and self.task.calculate_color_percentage(_LIBERATION_GOLD, icon) < 0.05):
            return False
        return super().liberation_available(check_color=False)

    def perform_heavy(self):
        self.check_combat()
        self.task.mouse_down()
        try:
            if not self.task.wait_until(self.heavy_in_progress, time_out=self.HEAVY_START_TIMEOUT):
                self.logger.warning("Lily heavy channel did not start")
                return False
            finished = self.task.wait_until(
                lambda: not self.heavy_in_progress(), time_out=self.HEAVY_TIMEOUT, settle_time=0.15,
            )
        finally:
            self.task.mouse_up()
        if not finished:
            self.logger.warning("Lily heavy channel did not finish before timeout")
            return False
        self.sleep(0.7)  # The prompt disappears at the start of the finisher.
        return True
