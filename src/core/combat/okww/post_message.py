"""OKWW's battle input sequence for WWA's Win32 game window."""

import logging

import win32api
import win32con
import win32gui
from pywintypes import error as Win32Error
from winerror import ERROR_INVALID_WINDOW_HANDLE

from src.util.keymouse_util import KEYBOARD_VK_MAPPING

logger = logging.getLogger(__name__)

_MOUSE_MESSAGES = {
    "left": (win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, win32con.WM_LBUTTONUP),
    "right": (win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, win32con.WM_RBUTTONUP),
    "middle": (win32con.WM_MBUTTONDOWN, win32con.MK_MBUTTON, win32con.WM_MBUTTONUP),
}


class PostMessageCombatInput:
    def __init__(self, window, controls=None):
        self._window = window
        self._controls = controls
        self._position = (0, 0)
        self._keys = {}
        self._buttons = {}

    @staticmethod
    def _activate(hwnd):
        win32gui.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    def key_down(self, key):
        if key in self._keys:
            hwnd, vk, params = self._keys[key]
        else:
            hwnd = self._window.window
            binding = self._controls.get_key(key, key) if self._controls is not None else key
            vk = binding if isinstance(binding, int) else KEYBOARD_VK_MAPPING.get(binding)
            if vk is None:
                raise ValueError(f"Unsupported OKWW combat binding for {key}: {binding!r}")
            params = (win32api.MapVirtualKey(vk, 0) << 16) | 1
        self._activate(hwnd)
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, params)
        self._keys[key] = hwnd, vk, params

    def key_up(self, key):
        if key in self._keys:
            hwnd, vk, params = self._keys[key]
            self._post_release(hwnd, win32con.WM_KEYUP, vk, params | (3 << 30))
            del self._keys[key]

    def mouse_down(self, button):
        if button in self._buttons:
            hwnd, position = self._buttons[button]
        else:
            hwnd, position = self._window.window, win32api.MAKELONG(*self._position)
        self._activate(hwnd)
        self._press_button(button, hwnd, position)

    def _press_button(self, button, hwnd, position):
        down, state, _ = _MOUSE_MESSAGES[button]
        win32gui.PostMessage(hwnd, down, state, position)
        self._buttons[button] = hwnd, position

    def mouse_up(self, button):
        if button in self._buttons:
            hwnd, position = self._buttons[button]
            self._post_release(hwnd, _MOUSE_MESSAGES[button][2], 0, position)
            del self._buttons[button]

    def click(self, button, down_time, wait, *, position=None):
        if button in self._buttons:
            wait(down_time)
            return
        hwnd = self._window.window
        if position is not None:
            self._position = position
        point = win32api.MAKELONG(*self._position)
        self._activate(hwnd)
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, point)
        wait(down_time)
        self._press_button(button, hwnd, point)
        try:
            wait(down_time)
        finally:
            self.mouse_up(button)

    @staticmethod
    def _post_release(hwnd, message, wparam, lparam):
        try:
            win32gui.PostMessage(hwnd, message, wparam, lparam)
        except Win32Error as error:
            if error.winerror != ERROR_INVALID_WINDOW_HANDLE:
                raise

    def release_inputs(self):
        for release, pressed in ((self.key_up, self._keys), (self.mouse_up, self._buttons)):
            for key in tuple(pressed):
                try:
                    release(key)
                except Exception:
                    logger.exception("Failed to release OKWW combat input %s", key)
