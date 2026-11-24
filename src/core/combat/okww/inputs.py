"""Interruptible combat actions using OKWW's message sequence."""

import time


class CombatInput:
    def _wait(self, seconds):
        deadline = time.monotonic() + max(0, seconds)
        while (remaining := deadline - time.monotonic()) > 0:
            self._checkpoint()
            time.sleep(min(0.025, remaining))
        self._checkpoint()

    def sleep(self, seconds):
        self._wait(seconds)
        if seconds >= 0.1:
            self.check_combat()

    def wait_until(self, condition, time_out=0, pre_action=None, post_action=None, raise_if_not_found=False,
                   *, settle_time=0):
        deadline = time.monotonic() + (time_out if time_out > 0 else 10)
        matched_since = None
        if pre_action is not None:
            pre_action()
        while True:
            self.next_frame()
            result = condition()
            now = time.monotonic()
            if result:
                if matched_since is None:
                    matched_since = now
                if now - matched_since >= settle_time:
                    return result
            else:
                matched_since = None
            if now >= deadline:
                break
            if post_action is not None:
                post_action()
        if raise_if_not_found:
            self.raise_not_in_combat("Combat condition timed out")
        return False

    def _can_send(self, interval):
        self._checkpoint()
        now = time.monotonic()
        if interval > 0 and now - self._last_input < interval:
            return False
        if interval > 0:
            self._last_input = now
        return True

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        if not self._can_send(interval):
            return False
        self.send_key_down(key)
        try:
            self._wait(down_time)
        finally:
            self.send_key_up(key)
        if after_sleep > 0:
            self.sleep(after_sleep)
        return True

    def send_key_down(self, key, after_sleep=0):
        self._checkpoint()
        self._input.key_down(str(key).upper())
        if after_sleep > 0:
            self.sleep(after_sleep)

    def send_key_up(self, key, after_sleep=0):
        self._input.key_up(str(key).upper())
        if after_sleep > 0:
            self.sleep(after_sleep)

    def mouse_down(self, key="left"):
        self._checkpoint()
        self._input.mouse_down(key)

    def mouse_up(self, key="left"):
        self._input.mouse_up(key)

    def click(self, interval=-1, after_sleep=0, key="left", down_time=0.02):
        if not self._can_send(interval):
            return False
        self._input.click(key, down_time, self._wait)
        if after_sleep > 0:
            self.sleep(after_sleep)
        return True

    def middle_click_relative(self, x, y, down_time=0.01, move_back=False):
        self._checkpoint()
        position = self.width_of_screen(x), self.height_of_screen(y)
        self._input.click("middle", down_time, self._wait, position=position)

    def jump(self, after_sleep=0):
        self.send_key("SPACE", after_sleep=after_sleep)

    def pickup_if_enabled(self):
        now = time.monotonic()
        if self.auto_pickup and not self.in_liberation and now - self._last_pickup >= 0.25:
            self._last_pickup = now
            self.send_key("F", down_time=0.01)

    def release_inputs(self):
        self._input.release_inputs()
