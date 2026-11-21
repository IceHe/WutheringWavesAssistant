import time
from .BaseChar import BaseChar, SwitchPriority


class Mornye(BaseChar):
    FIELD_TIME = 10.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_heavy = 0
        self.echo_lit_on_air = False

    def do_perform(self):
        started = time.monotonic()
        deadline = started + self.FIELD_TIME
        if self.has_intro:
            self.continues_normal_attack(1.33)
        self.check_f_on_switch = True
        if not self.on_air():
            if self.combo_limit():
                self.logger.debug("perform quick actions")
                if self.click_echo():
                    return self.switch_next_char()
                elif self.click_resonance()[0]:
                    """地面奶一下，防止主C死亡"""
                    return self.switch_next_char()
                else:
                    self.continues_normal_attack(0.1)
                    return self.switch_next_char()
            self.not_on_air_actions(deadline)

        if time.monotonic() < deadline and self.on_air():
            self.on_air_actions(deadline)
        elapsed = time.monotonic() - started
        if elapsed >= self.FIELD_TIME:
            self.logger.info("Mornye field time reached %.1fs, switching out", elapsed)
        self.switch_next_char()

    def on_air(self):
        return self.has_long_action2()

    def on_air_actions(self, deadline):
        self.echo_lit_on_air = False
        self.logger.debug("on_air start attacking")
        heavy_attempts = 0
        heavy_confirmed = False
        while time.monotonic() < deadline and self.on_air():
            if self.detect_elbow_strike():
                self.logger.debug("Detected an elbow strike, attempting to reset.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.task.wait_until(lambda: not self.detect_elbow_strike(),
                                     post_action=lambda: self.continues_right_click(0.05),
                                     time_out=min(1.5, remaining))
            if time.monotonic() >= deadline:
                break
            self.click_liberation()
            if time.monotonic() >= deadline:
                break
            if self.on_air() and self.is_mouse_forte_full():
                self.logger.debug("mouse forte full, heavy attack")
                heavy_attempts += 1
                if self.heavy_click_forte(
                        check_fun=lambda: self.is_mouse_forte_full() and not self.detect_elbow_strike()):
                    if self.detect_elbow_strike():
                        continue
                    heavy_confirmed = True
                    # 协奏未满则先普攻补满（最多 2s），仍不满再放声骸
                    remaining = deadline - time.monotonic()
                    if remaining > 0 and not self.task.wait_until(self.is_con_full, time_out=min(1.5, remaining)):
                        fill_deadline = min(deadline, time.monotonic() + 2)
                        while time.monotonic() < fill_deadline and not self.is_con_full() and self.on_air():
                            self.continues_normal_attack(0.5)
                            if self.detect_elbow_strike():
                                break
                        if time.monotonic() < deadline and not self.is_con_full():
                            self.click_echo(duration=0.2)
                    self.last_heavy = time.time()
                    self.check_f_on_switch = False
                    break
            if time.monotonic() >= deadline:
                break
            self.click_resonance()
            self.click()
            self.sleep(0.01)
        self.logger.debug("finished attacking on_air")
        if heavy_attempts and not heavy_confirmed:
            self.logger.info("Mornye aerial heavy attack was not confirmed after %d attempt(s)", heavy_attempts)

    def not_on_air_actions(self, deadline):
        self.logger.debug("not on_air start attacking")
        try_dodge = True
        while time.monotonic() < deadline and not self.on_air():
            self.click()
            self.click_resonance()
            self.check_combat()
            self.sleep(0.1)
            if time.monotonic() < deadline and self.is_mouse_forte_full():
                if try_dodge:
                    self.task.click(key="right")
                    try_dodge = False
                self.heavy_attack()
                self.sleep(0.3)

    def on_combat_end(self, chars):
        self.switch_other_char()

    def combo_limit(self):
        return self.time_elapsed_accounting_for_freeze(self.last_heavy) < 23

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_aemeath', 'char_qingxiao'}:
            return SwitchPriority.MUST
        from .Linnai import Linnai
        if has_intro and current_char and self.task.has_char(Linnai) and current_char.char_name != 'char_linnai':
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def detect_elbow_strike(self):
        # 声骸图标在空中亮过再变暗才算被击飞，起飞过渡中或空中不能放的声骸图标本来就是暗的
        echo_lit = self.task.box_highlighted('echo')
        if (echo_lit or self.echo_lit_on_air) and self.has_cd('echo'):
            self.echo_lit_on_air = False
            return False
        if echo_lit:
            self.echo_lit_on_air = True
            return False
        return self.echo_lit_on_air
