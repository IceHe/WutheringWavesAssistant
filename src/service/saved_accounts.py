"""Select an already saved game account, independently of daily and combat input."""

import ctypes
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import cv2
import win32api
import win32con
import win32gui
import win32process
from pywintypes import error as Win32Error

from src.config.config import ACCOUNT_SUFFIX_PATTERN
from src.core.exceptions import AccountLoggedInElsewhere, ScreenshotError
from src.core.geometry import BBox, TextBox
from src.core.i18n import I18nText
from src.core.pages import GlobalPage, UIOp, is_account_logged_in_elsewhere
from src.core.resource import Resource
from src.util import hwnd_util, img_util

logger = logging.getLogger(__name__)

_LOGIN_TEXT = r"^(?:登录|登錄|登入|log\s*in)$"
_VERIFICATION_TEXT = r"请输入(?:短信)?验证码|安全验证|請輸入驗證碼|security verification|enter.+verification code|captcha"
_RETURN_ACTION = (r"返回\s*(?:至\s*)?(?:登\s*[录錄入]|标题|標題)(?:界面|畫面)?|"
                  r"return\s+to\s+(?:the\s+)?(?:log\s*in|title)(?:\s+screen)?")
_QUIT_GAME = r"退出\s*(?:游戏|遊戲)|(?:exit|quit)\s+(?:the\s+)?game"
_LOGOUT_PROMPT = r"^\s*[确確]\s*[认認]\s*登\s*出\s*[账帳]\s*[号號]\s*[?？]?\s*$"
_LOGOUT_CONFIRM = r"^\s*[确確]\s*[认認]\s*登\s*出\s*$"


def _suffix(label, *, ocr=False, configured=()):
    text = unicodedata.normalize("NFKC", label).strip()
    if not (match := re.search(rf"{ACCOUNT_SUFFIX_PATTERN}\Z", text)):
        return None
    suffix = match[0].upper()
    if not ocr or any(mark in text for mark in "*•●"):
        return suffix
    # QR logins can show an unmasked ID. Reject ordinary UI text on the same panel.
    if len(text) == 4 and suffix in configured:
        return suffix
    if text.isalnum() and not (suffix.isdigit() or suffix.isalpha()):
        return suffix
    return None


def _message(hwnd, message, wparam=0, lparam=0):
    _, result = win32gui.SendMessageTimeout(hwnd, message, wparam, lparam, win32con.SMTO_ABORTIFHUNG, 500)
    return ctypes.c_ssize_t(result).value


def _combo_accounts(combo):
    suffixes = []
    for index in range(max(0, _message(combo, win32con.CB_GETCOUNT))):
        size = _message(combo, win32con.CB_GETLBTEXTLEN, index)
        if not 0 < size <= 512:
            suffixes.append(None)
            continue
        text = ctypes.create_unicode_buffer(size + 1)
        _message(combo, win32con.CB_GETLBTEXT, index, ctypes.addressof(text))
        suffixes.append(_suffix(text.value))
    return suffixes


@lru_cache(maxsize=1)
def _switch_account_icon():
    # OKWW fac98810, assets/templates/switch_account_1920x1080_1796_533.png.
    return img_util.read_img(Resource.Template / "switch_account_1920x1080_1796_533.png", alpha=False)


def _switch_account_point(frame):
    height, width = frame.shape[:2]
    x1, y1 = int(width * 0.84), int(height * 0.36)
    region = frame[y1:int(height * 0.66), x1:]
    icon = cv2.resize(_switch_account_icon(), None, fx=height / 1080, fy=height / 1080)
    if region.shape[0] < icon.shape[0] or region.shape[1] < icon.shape[1]:
        return None
    _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(region, icon, cv2.TM_CCOEFF_NORMED))
    if score >= 0.74:
        return x1 + point[0] + icon.shape[1] // 2, y1 + point[1] + icon.shape[0] // 2
    return None


@dataclass(frozen=True)
class _AccountPanel:
    hwnd: int
    login: TextBox | None
    rows: tuple[TextBox, ...]
    combo: int | None = None


class SavedAccountSwitcher:
    def __init__(self, ctx, account_suffixes=()):
        self.ctx = ctx
        self.ui = UIOp(ctx)
        self.pages = GlobalPage(ctx)
        self._account_suffixes = frozenset(account_suffixes)

    def enter(self, suffix):
        """Enter the world after verifying the selected saved account suffix."""
        panel = self._open_panel()
        if panel.combo is not None:
            self._select_native(panel.combo, suffix)
        else:
            self._select_visible(panel, suffix)

        def verified_panel():
            current = self._read_panel()
            if current is None or current.hwnd != panel.hwnd or current.login is None:
                return None
            if self._selected_suffix(current) == suffix:
                return current
            return None

        if not (confirmed := self.ui.wait(5, 0.5).until(verified_panel)):
            raise RuntimeError(f"Could not verify saved account ending in {suffix}")
        self._click(confirmed.hwnd, confirmed.login.center)
        if not self.ui.sleep(2).wait(120, 0.75).until(self._enter_world):
            raise RuntimeError(f"Account ending in {suffix} did not enter the game; check the game login screen")

    def _click(self, hwnd, point):
        self.ui.sleep(0.1)
        control = self.ctx.control_service
        control.activate_window(hwnd)
        if hwnd != self.ctx.window_service.window:
            child = win32gui.ChildWindowFromPointEx(
                hwnd, point, win32con.CWP_SKIPDISABLED | win32con.CWP_SKIPINVISIBLE)
            if child and child != hwnd:
                point = win32gui.ScreenToClient(child, win32gui.ClientToScreen(hwnd, point))
                hwnd = child
        control.click_window(hwnd, *point)

    def _return_login_button(self, frame, ocr):
        height, width = frame.shape[:2]
        body = BBox(int(width * 0.23), int(height * 0.34), int(width * 0.77), int(height * 0.57))
        left = BBox(int(width * 0.20), int(height * 0.55), width // 2, int(height * 0.76))
        right = BBox(width // 2, left.y1, int(width * 0.80), left.y2)

        # The current dialog offers Quit Game and Return to Login as separate actions.
        if buttons := ocr.search(rf"^\s*(?:{_RETURN_ACTION})\s*$", right):
            if len(buttons) == 1 and (ocr.search(_QUIT_GAME, body) or ocr.search(rf"^\s*(?:{_QUIT_GAME})\s*$", left)):
                return buttons[0]
        # A generic Confirm is safe only when the prompt explicitly says where it returns.
        if ocr.search(_RETURN_ACTION, body) and ocr.search(self.ctx.tr(I18nText.Cancel), left):
            confirms = ocr.search(self.ctx.tr(I18nText.Confirm), right) or []
            if len(confirms) == 1:
                return confirms[0]
        return None

    def _handle_logout_dialog(self, frame, ocr):
        height, width = frame.shape[:2]
        body = BBox(int(width * 0.23), int(height * 0.34), int(width * 0.77), int(height * 0.55))
        if not ocr.search(_LOGOUT_PROMPT, body):
            return False

        right = BBox(width // 2, body.y2, int(width * 0.80), int(height * 0.76))
        buttons = ocr.search(_LOGOUT_CONFIRM, right) or []
        if len(buttons) == 1:
            self.ui.click_bbox(buttons[0], delay=0.2).sleep(0.8)
            logger.info("Confirmed logout before opening saved accounts")
        return True

    def _open_panel(self):
        if panel := self._read_panel():
            return panel
        self.ui.activate()

        def terminal_or_title():
            frame = self.ui.grap()
            ocr = self.ctx.ocr_service.query(frame)
            if self._handle_account_disconnect(frame, ocr):
                return None
            if self._handle_logout_dialog(frame, ocr):
                return "title", None
            if button := self._return_login_button(frame, ocr):
                return "return_dialog", button
            if self.pages.handle_login_rewards(ui=self.ui, frame=frame, ocr=ocr):
                return None
            if self.pages.isTerminal(ui=ocr):
                return "terminal", frame
            if self._is_title(ocr):
                return "title", frame
            if panel := self._read_panel(frame):
                return "panel", panel
            self.ui.esc().sleep(0.8)
            return None

        reached = self.ui.wait(12, 0.5).until(terminal_or_title)
        if not reached:
            raise RuntimeError("Could not find the game terminal or saved-account screen")
        page, target = reached
        if page == "panel":
            return target
        if page == "terminal":
            height, width = target.shape[:2]
            self.ui.click(int(width * 0.04), int(height * 0.96))

            def read_return_dialog():
                frame = self.ui.grap()
                ocr = self.ctx.ocr_service.query(frame)
                if self._handle_account_disconnect(frame, ocr):
                    return "disconnected", None
                if button := self._return_login_button(frame, ocr):
                    return "return_dialog", button
                return None

            reached = self.ui.sleep(0.5).wait(8, 0.5).until(read_return_dialog)
            if not reached:
                raise RuntimeError("Could not find the Return to Login button after opening the exit dialog")
            page, target = reached
        if page == "return_dialog":
            self.ui.click_bbox(target)
            logger.info("Clicked Return to Login in the game exit dialog")

        title_clicked = False

        def open_saved_accounts():
            nonlocal title_clicked
            if panel := self._read_panel():
                return panel
            frame = self.ui.grap()
            ocr = self.ctx.ocr_service.query(frame)
            if self._handle_account_disconnect(frame, ocr) or self._handle_logout_dialog(frame, ocr):
                title_clicked = False
            elif not title_clicked and self._is_title(ocr):
                buttons = ocr.search(self.ctx.tr(I18nText.Login)) or ocr.search(self.ctx.tr(I18nText.Account))
                point = buttons[0].center if buttons else _switch_account_point(frame)
                if point is not None:
                    self.ui.click_point(point).sleep(1)
                    title_clicked = True
            return None

        if panel := self.ui.sleep(1).wait(45, 0.75).until(open_saved_accounts):
            return panel
        raise RuntimeError("Saved-account list not found; check whether the game requires manual verification")

    def _is_title(self, ocr):
        return bool(ocr.search(self.ctx.tr([I18nText.Settings, I18nText.Bulletin]))
                    and ocr.search(self.ctx.tr([I18nText.Login, I18nText.TapToLandInSolaris3, I18nText.Account])))

    def _read_panel(self, main_frame=None, *, dropdown_hwnd=None):
        main = self.ctx.window_service.window
        pid = win32process.GetWindowThreadProcessId(main)[1]
        dialogs = []

        def collect(hwnd, _):
            if hwnd != main and win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                _, _, width, height = win32gui.GetClientRect(hwnd)
                if width >= 240 and height >= 120:
                    dialogs.append(hwnd)
            return True

        win32gui.EnumWindows(collect, None)
        for hwnd in (*dialogs, main):
            try:
                if hwnd == main:
                    frame = main_frame if main_frame is not None else self.ui.grap()
                else:
                    frame = self.ctx.img_service.screenshot_window(hwnd)
                ocr = self.ctx.ocr_service.query(frame)
                height, width = frame.shape[:2]
                roi = None
                if hwnd == main:
                    roi = BBox(int(width * 0.25), int(height * 0.24), int(width * 0.75), int(height * 0.84))
                if ocr.search(_VERIFICATION_TEXT, roi):
                    raise RuntimeError("The game requires manual account verification")
                login = ocr.search(_LOGIN_TEXT, roi) or []
                if not login and hwnd != dropdown_hwnd:
                    continue
                rows = tuple(sorted((box for box in ocr.results
                                     if (roi is None or roi.contains_bbox(box)) and self._ocr_suffix(box.text)),
                                    key=lambda box: box.y1))
                combo = self._find_combo(hwnd) if hwnd != main else None
                if combo or rows:
                    return _AccountPanel(hwnd, login[0] if login else None, rows, combo)
            except (ScreenshotError, Win32Error):
                continue  # A dialog can disappear between enumeration and capture.
        return None

    def _find_combo(self, hwnd):
        for child in hwnd_util.get_child_hwnds(hwnd):
            if win32gui.GetClassName(child) == "ComboBox" and win32gui.IsWindowVisible(child):
                try:
                    suffixes = _combo_accounts(child)
                    if any(suffixes) and (not self._account_suffixes or not self._account_suffixes.isdisjoint(suffixes)):
                        return child
                except Win32Error:
                    continue
        return None

    @staticmethod
    def _select_native(combo, suffix):
        matches = [i for i, value in enumerate(_combo_accounts(combo)) if value == suffix]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one saved account ending in {suffix}, found {len(matches)}")
        index = matches[0]
        if _message(combo, win32con.CB_GETCURSEL) != index:
            _message(combo, win32con.CB_SETCURSEL, index)
            command = win32api.MAKELONG(win32gui.GetDlgCtrlID(combo), win32con.CBN_SELCHANGE)
            _message(win32gui.GetParent(combo), win32con.WM_COMMAND, command, combo)

    def _select_visible(self, panel, suffix):
        if len(panel.rows) == 1:
            self._click(panel.hwnd, panel.rows[0].center)

        def opened_list():
            current = self._read_panel(dropdown_hwnd=panel.hwnd)
            if current and current.hwnd == panel.hwnd and len(current.rows) > 1:
                return current
            return None

        opened = self.ui.wait(6, 0.4).until(opened_list)
        if not opened:
            raise RuntimeError("Could not open the saved-account dropdown")
        # The first row is the field, followed by the selectable saved entries.
        matches = [row for row in opened.rows[1:] if self._ocr_suffix(row.text) == suffix]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one saved account ending in {suffix}, found {len(matches)}")
        self._click(opened.hwnd, matches[0].center)

    def _ocr_suffix(self, label):
        return _suffix(label, ocr=True, configured=self._account_suffixes)

    def _selected_suffix(self, panel):
        if panel.combo is not None:
            index = _message(panel.combo, win32con.CB_GETCURSEL)
            accounts = _combo_accounts(panel.combo)
            return accounts[index] if 0 <= index < len(accounts) else None
        if len(panel.rows) == 1:
            return self._ocr_suffix(panel.rows[0].text)
        return None

    def _enter_world(self):
        frame = self.ui.grap()
        ocr = self.ctx.ocr_service.query(frame)
        if is_account_logged_in_elsewhere(self.ctx, ocr):
            raise AccountLoggedInElsewhere()
        if self.pages.handle_login_rewards(ui=self.ui, frame=frame, ocr=ocr):
            return False
        if self.ui.is_on_homepage(frame):
            return True
        if self._is_title(ocr) and self._read_panel(frame) is None:
            if connect := ocr.search(self.ctx.tr(I18nText.TapToLandInSolaris3)):
                self.ui.click_bbox(connect).sleep(3)
        return False

    def return_after_disconnect(self):
        self.ui.activate()

        def login_ready():
            frame = self.ui.grap()
            ocr = self.ctx.ocr_service.query(frame)
            if self._handle_account_disconnect(frame, ocr) or self._handle_logout_dialog(frame, ocr):
                return False
            return self._is_title(ocr) or self._read_panel(frame) is not None

        if not self.ui.wait(30, 0.75).until(login_ready):
            raise RuntimeError("Could not return to login after the account was logged in elsewhere")

    def _handle_account_disconnect(self, frame, ocr):
        if not is_account_logged_in_elsewhere(self.ctx, ocr):
            return False
        height, width = frame.shape[:2]
        buttons = BBox(width // 2, height // 2, round(width * 0.80), round(height * 0.75))
        confirm = ocr.search(self.ctx.tr(I18nText.Confirm), buttons)
        if confirm and len(confirm) == 1:
            self.ui.click_bbox(confirm[0], delay=0.2).sleep(0.8)
        return True
