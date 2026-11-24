"""CombatSystem-compatible lifecycle for OKWW's character rotation."""

import logging
import threading
import time

from src.core.combat.combat_core import BaseResonator
from src.core.combat.combat_system import CombatSystem
from src.core.exceptions import StopError

from .post_message import PostMessageCombatInput
from .rotation import NotInCombatException
from .runtime import CombatRuntime
from .vision import template_index

logger = logging.getLogger(__name__)

_FOREGROUND_GRACE_SECONDS = 1.0


class _CombatFocusLost(StopError):
    pass


class OKWWCombatSystem:
    def __init__(self, control, images, window, ocr, task_event, *, controls=None, open_world=False):
        template_index()
        self._workflow_control = control
        self.img_service = images
        self.is_async = False
        self.auto_pickup = False
        self.check_boss_hp = True
        self.resonators = None
        self._task_event = task_event
        self._open_world = open_world
        self._window = window
        self._lock = threading.Lock()
        self._thread = None
        self._active = False
        self._generation = 0
        self._running_generation = 0
        self._deadline = None
        self._reset_camera = True
        self._idle = threading.Event()
        self._idle.set()
        inputs = PostMessageCombatInput(window, controls)
        self._runtime = CombatRuntime(inputs, images, ocr, self._checkpoint, open_world=open_world)
        self._special_states = None

    @property
    def is_paused(self):
        return self._idle.is_set()

    @property
    def combat_check_suspended(self):
        return self._runtime.combat_check_suspended

    def set_resonators(self, resonator_names_zh, is_print=True):
        hints = tuple(name if name and name.casefold() != "none" else None for name in resonator_names_zh)
        with self._lock:
            previous = self.resonators
            self.resonators = hints
            if previous != hints and self._active:
                self._generation += 1
        if is_print:
            logger.info("OKWW team hints: %s", hints)

    def start(self, delay_seconds=0.0, *, reset_camera=True):
        with self._lock:
            if self._task_event is not None and not self._task_event.is_set():
                return
            now = time.monotonic()
            if not self._active or (self._deadline is not None and now >= self._deadline):
                self._active = True
                self._generation += 1
                self._reset_camera = reset_camera
            self._deadline = now + delay_seconds if delay_seconds > 0 else None
            if self._thread is not None:
                return
            self._idle.clear()
            if self.is_async:
                self._thread = threading.Thread(target=self._run, name="OKWWCombat", daemon=True)
                self._thread.start()
                return
            self._thread = threading.current_thread()
        self._run()

    def pause(self, join=False, timeout=5):
        with self._lock:
            self._active = False
            self._generation += 1
            worker = self._thread
        if join and worker is not None and worker is not threading.current_thread():
            if not self._idle.wait(timeout):
                logger.warning("OKWW worker is still releasing combat inputs")

    def stop(self, join=False):
        self.pause(join=join, timeout=3)

    def _check_session(self):
        with self._lock:
            cancelled = (not self._active or self._generation != self._running_generation
                         or (self._deadline is not None and time.monotonic() >= self._deadline))
        if cancelled or (self._task_event is not None and not self._task_event.is_set()):
            raise StopError()

    def _checkpoint(self):
        self._check_session()
        if self._open_world and not self._window.is_foreground_window():
            raise _CombatFocusLost()

    def _wait_for_foreground(self):
        deadline = time.monotonic() + _FOREGROUND_GRACE_SECONDS
        while not self._window.is_foreground_window():
            self._check_session()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.025)
        self._check_session()
        return True

    def _run(self):
        runtime = self._runtime
        while True:
            with self._lock:
                if not self._active:
                    self._thread = None
                    self._idle.set()
                    return
                generation = self._generation
                self._running_generation = generation
                hints = self.resonators or ()
                reset_camera = self._reset_camera
            try:
                while True:
                    try:
                        self._run_rotation(hints, reset_camera)
                    except _CombatFocusLost:
                        runtime.release_inputs()
                        logger.info("OKWW combat waiting for the game window to regain focus")
                        if not self._wait_for_foreground():
                            logger.info("OKWW combat paused: game window stayed out of the foreground")
                            break
            except StopError:
                pass
            except NotInCombatException as error:
                logger.info("OKWW combat paused: %s", error)
            except Exception:
                logger.exception("OKWW combat stopped after a backend error")
            finally:
                runtime.release_inputs()
                with self._lock:
                    if generation == self._generation:
                        self._active = False

    def _run_rotation(self, hints, reset_camera):
        runtime = self._runtime
        runtime.reset_session()
        runtime.hints = hints
        armed = False
        while True:
            runtime.auto_pickup = self.auto_pickup
            runtime.check_boss_hp = self.check_boss_hp
            runtime.next_frame()
            if not runtime.load_chars():
                runtime._wait(0.15)
                continue
            runtime.check_combat(fresh=False)
            if not armed:
                self._checkpoint()
                if reset_camera and (not self._open_world or not runtime.has_target()):
                    runtime.click(key="middle")
                armed = True
            runtime.get_current_char().perform()
            runtime.release_inputs()
            runtime.pickup_if_enabled()

    def is_boss_health_bar_exist(self, img=None):
        return BaseResonator.is_boss_health_bar_exist(self.img_service.screenshot() if img is None else img)

    def boss_immobilized_bar_exist(self, img=None):
        return BaseResonator.boss_immobilized_bar_exist(self.img_service.screenshot() if img is None else img)

    def exit_special_state(self, morph):
        # WWA workflows use these actions to make navigation safe outside combat.
        self.pause(join=True)
        if not self.is_paused:
            return False
        if self._task_event is not None and not self._task_event.is_set():
            return False
        if self._special_states is None:
            self._special_states = CombatSystem(self._workflow_control, self.img_service)
        hints = list(self.resonators or ())
        for i, name in enumerate(hints):
            if name is None:
                hints[i] = "None"
        self._special_states.set_resonators(hints, is_print=False)
        return self._special_states.exit_special_state(morph)
