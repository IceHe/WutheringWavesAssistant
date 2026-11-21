"""Select a combat implementation from the task's configuration snapshot."""

import logging

from src.config.config import CombatBackend
from src.core.combat.combat_system import CombatSystem

logger = logging.getLogger(__name__)


def combat_backend(spec) -> CombatBackend:
    options = getattr(spec, "user_config", None) or {}
    return CombatBackend(options.get("Game", {}).get("CombatBackend", CombatBackend.WWA))


def create_combat_system(context, control, images, *, open_world=False):
    backend = combat_backend(getattr(context, "spec", None))
    logger.info("Character combat backend: %s", backend)
    if backend == CombatBackend.WWA:
        return CombatSystem(control, images)

    try:
        from src.core.combat.okww.backend import OKWWCombatSystem

        runtime = getattr(context, "runtime", None)
        window = context._container.window_service()
        ocr = context._container.ocr_service
        if runtime is not None:
            task_event = runtime.stop_event
            config = runtime.cfg
        else:
            # The older page-event flow has no RuntimeConfig in its Context.
            # Use its task snapshot for combat bindings and WWA's special-state actions.
            from src.core.runtime import RuntimeConfig
            from src.core.workflow import NodeContext, RuntimeContext
            from src.service.control_service import Win32ControlServiceImpl

            task_event = context._combat_task_event
            config = RuntimeConfig(context.spec.user_config)
            input_context = NodeContext(runtime=RuntimeContext(cfg=config, stop_event=task_event))
            control = Win32ControlServiceImpl(input_context, window)
        return OKWWCombatSystem(control, images, window, ocr, task_event,
                                controls=config.controls, open_world=open_world)
    except Exception:
        logger.exception("Could not initialize OKWW character combat")
        raise
