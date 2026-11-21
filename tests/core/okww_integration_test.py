import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.config.config import CombatBackend, Config
from src.core.combat.backend import combat_backend, create_combat_system
from src.core.combat.combat_system import CombatSystem
from src.core.combat.okww.backend import OKWWCombatSystem
from src.core.runtime import RuntimeConfig
from src.core.workflow import TaskSpec
from src.service.combat_service import CombatServiceImpl


def task_spec(backend=None):
    game = {} if backend is None else {"CombatBackend": backend}
    return TaskSpec(user_config=Config.from_dict({"Game": game}).to_dict())


class CombatSelectionTest(unittest.TestCase):
    def test_old_config_uses_wwa(self):
        context = SimpleNamespace(spec=task_spec())
        self.assertEqual(combat_backend(context.spec), CombatBackend.WWA)
        self.assertIsInstance(create_combat_system(context, Mock(), Mock()), CombatSystem)

    def test_node_legacy_and_combat_service_use_same_task_option(self):
        for node_context in (True, False):
            with self.subTest(node_context=node_context):
                event = threading.Event()
                event.set()
                window = Mock()
                context = SimpleNamespace(
                    spec=task_spec("OKWW"),
                    _container=Mock(window_service=lambda: window),
                    _combat_task_event=event,
                )
                context.spec.user_config["Controls"]["ResonanceSkill"] = "K"
                if node_context:
                    context.runtime = SimpleNamespace(stop_event=event, cfg=RuntimeConfig(context.spec.user_config))
                service = CombatServiceImpl(context, window, Mock(), Mock(), Mock())
                combat = service.combat_system()
                self.assertIsInstance(combat, OKWWCombatSystem)
                self.assertIs(combat._task_event, event)
                self.assertEqual(combat._runtime._input._controls.resonanceSkill, "K")

    def test_cli_and_gui_submission_share_the_task_snapshot(self):
        from src import application
        from src.controller.main_controller import MainController
        from src.util import file_util

        selected = []

        def start_task(*, args, **options):
            event, spec, _ = args
            def start():
                selected.append(combat_backend(spec))
                event.clear()

            return Mock(start=start)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "param-config.json"
            path.write_text('{"Game": {"CombatBackend": "OKWW"}}', encoding="utf-8")
            monitor = Mock(game_path="game.exe", launch_info=None)

            with (patch.object(file_util, "get_temp_config", return_value=path),
                  patch("src.controller.main_controller.ProcessBridge"),
                  patch("src.controller.main_controller.TaskMonitor", return_value=monitor),
                  patch("src.core.tasks.ExploreTask.build", side_effect=start_task)):
                main = MainController()
                self.assertTrue(main.execute("ExploreTask", "START")[0])
                with (patch.object(application, "before"),
                      patch("sys.argv", ["main.py", "--task", "ExploreTask"])):
                    application.run()

        self.assertEqual(selected, [CombatBackend.OKWW, CombatBackend.OKWW])

    def test_boss_task_dispatch_preserves_the_selected_backend(self):
        from src.core.tasks import boss_task

        for bosses, path in [(["Dreamless"], "_boss_task"),
                             (["Dreamless", "Jue"], "auto_boss_task_run")]:
            with self.subTest(bosses=bosses):
                spec = task_spec("OKWW")
                spec.user_config["BossRush"]["BossName"] = bosses
                with patch(f"src.core.tasks.{path}") as run:
                    boss_task(None, spec, None)
                run.assert_called_once_with(None, spec, None)
                self.assertEqual(combat_backend(spec), CombatBackend.OKWW)

    def test_initialization_error_is_reported(self):
        context = SimpleNamespace(spec=task_spec("OKWW"), _container=Mock())
        context._container.window_service.side_effect = RuntimeError("no window")
        with self.assertLogs("src.core.combat.backend", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "no window"):
                create_combat_system(context, Mock(), Mock())


class CombatSettingsTest(unittest.TestCase):
    def test_qt_selector_restores_option_saved_for_next_task(self):
        from PySide6.QtCore import QTranslator
        from PySide6.QtWidgets import QApplication

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication([])
        from src.gui.common.config import paramConfig
        from src.gui.view.home_interface import BasicSettingWidget

        translations = Path(__file__).resolve().parents[2] / "src/gui/resource/i18n/gallery.zh_CN.qm"
        translator = QTranslator()
        self.assertTrue(translator.load(str(translations)))
        app.installTranslator(translator)
        self.addCleanup(app.removeTranslator, translator)

        original = paramConfig.get(paramConfig.combatBackend)
        self.addCleanup(paramConfig.set, paramConfig.combatBackend, original, False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "param-config.json"
            with patch.object(paramConfig._cfg, "file", path):
                paramConfig.set(paramConfig.combatBackend, "WWA", save=False)
                widget = BasicSettingWidget()
                box = widget.combatBackendComboBox
                self.assertEqual(box.currentData(), "WWA")
                self.assertEqual(widget.formLayout.labelForField(box).text(), "\u89d2\u8272\u6218\u6597\u903b\u8f91:")
                box.setCurrentIndex(box.findData("OKWW"))
                spec = TaskSpec(user_config=Config.load(path).to_dict())
                self.assertEqual(combat_backend(spec), CombatBackend.OKWW)

                paramConfig.set(paramConfig.combatBackend, "WWA", save=False)
                paramConfig.load(path)
                reopened = BasicSettingWidget()
                self.assertEqual(reopened.combatBackendComboBox.currentData(), "OKWW")
                reopened.close()
                widget.close()
