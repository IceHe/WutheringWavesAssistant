"""Interruptible input through WWA's existing key mapping."""

import time

from src.util import keymouse_util


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

    def wait_until(self, condition, time_out=0, pre_action=None, post_action=None, raise_if_not_found=False):
        deadline = time.monotonic() + (time_out if time_out > 0 else 10)
        if pre_action is not None:
            pre_action()
        while True:
            self.next_frame()
            if result := condition():
                return result
            if time.monotonic() >= deadline:
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
            self._release_key(str(key).upper())
        if after_sleep > 0:
            self.sleep(after_sleep)
        return True

    def send_key_down(self, key, after_sleep=0):
        self._checkpoint()
        key = str(key).upper()
        self._pressed_keys.add(key)
        self.control.key_down(key)
        if after_sleep > 0:
            self.sleep(after_sleep)

    def _release_key(self, key):
        if key in self._pressed_keys:
            self.control.key_up(key)
            self._pressed_keys.discard(key)

    def send_key_up(self, key, after_sleep=0):
        self._release_key(str(key).upper())
        if after_sleep > 0:
            self.sleep(after_sleep)

    def mouse_down(self, key="left"):
        self._checkpoint()
        self._pressed_buttons.add(key)
        action = self.control.mouse_left_down if key == "left" else self.control.mouse_right_down
        action()

    def mouse_up(self, key="left"):
        if key in self._pressed_buttons:
            action = self.control.mouse_left_up if key == "left" else self.control.mouse_right_up
            action()
            self._pressed_buttons.discard(key)

    def click(self, interval=-1, after_sleep=0, key="left", down_time=0.02):
        if not self._can_send(interval):
            return False
        held = key in self._pressed_buttons
        if not held:
            self.mouse_down(key)
        try:
            self._wait(down_time)
        finally:
            if not held:
                self.mouse_up(key)
        if after_sleep > 0:
            self.sleep(after_sleep)
        return True

    def middle_click_relative(self, x, y, down_time=0.01, move_back=False):
        self._checkpoint()
        keymouse_util.middle_click(self.window.window, self.width_of_screen(x), self.height_of_screen(y),
                                   seconds=down_time)

    def jump(self, after_sleep=0):
        self.send_key("SPACE", after_sleep=after_sleep)

    def pickup_if_enabled(self):
        now = time.monotonic()
        if self.auto_pickup and not self.in_liberation and now - self._last_pickup >= 0.25:
            self._last_pickup = now
            self.send_key("F", down_time=0.01)

    def release_inputs(self):
        for key in tuple(self._pressed_keys):
            try:
                self._release_key(key)
            except Exception:
                self.logger.exception("Failed to release OKWW combat key %s", key)
        for button in tuple(self._pressed_buttons):
            try:
                self.mouse_up(button)
            except Exception:
                self.logger.exception("Failed to release OKWW combat mouse button %s", button)
