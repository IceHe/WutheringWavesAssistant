import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.config.config import Config, parse_daily_account_suffixes
from src.core.i18n import I18nText


class DailyAccountRowsTest(unittest.TestCase):
    def test_daily_rows_use_drop_order_and_preserve_each_account_selection(self):
        from PySide6.QtCore import QPointF, QTranslator
        from PySide6.QtWidgets import QApplication, QDialog

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication([])
        from src.gui.common.config import paramConfig
        from src.gui.view.home.daily import DailyWidget
        from src.gui.view.home.daily_account_rows import _NestSelectionDialog

        translator = QTranslator()
        translations = Path(__file__).resolve().parents[2] / "src/gui/resource/i18n/gallery.zh_CN.qm"
        self.assertTrue(translator.load(str(translations)))
        app.installTranslator(translator)
        self.addCleanup(app.removeTranslator, translator)

        items = (paramConfig.dailyMultiAccount, paramConfig.dailyAccountSuffixes,
                 paramConfig.dailyAccountTacetSuppression, paramConfig.dailyAccountTacetDiscordNest)
        original = [paramConfig.get(item) for item in items]
        for item, value in zip(items, original):
            self.addCleanup(paramConfig.set, item, value, False)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "param-config.json"
            with patch.object(paramConfig._cfg, "file", path):
                for item, value in zip(items, (False, "6663, 9892", {}, {})):
                    paramConfig.set(item, value, save=False)
                widget = DailyWidget()
                self.addCleanup(widget.close)
                widget.resize(850, 700)
                widget.show()
                rows = widget.dailyAccountRows
                self.assertTrue(rows.isHidden())
                widget.multiAccountCheckBox.setChecked(True)
                self.assertFalse(rows.isHidden())
                first, second = rows._rows()
                self.assertEqual(first.dungeon.currentText(), "沿用全局")
                self.assertEqual(first.nests.text(), "聚落：沿用全局")
                self.assertEqual(rows.configuration(require_minimum=True), ("6663, 9892", {}, {}))

                stillness, flames = I18nText.TacetFieldHeartOfStillness, I18nText.TacetFieldHeartOfFlames
                first.dungeon.setCurrentIndex(first.dungeon.findData(stillness))
                second.dungeon.setCurrentIndex(second.dungeon.findData(flames))
                rows.add_button.click()
                third = rows._rows()[2]
                third.suffix.setText("917")
                self.assertEqual(Config.load(path).daily.accountSuffixes, "6663, 9892")
                third.suffix.setText("917a")
                self.assertEqual(Config.load(path).daily.accountSuffixes, "6663, 9892, 917A")

                def edit_nests(row, choose):
                    def apply(dialog):
                        choose(dialog)
                        return QDialog.DialogCode.Accepted
                    with patch.object(_NestSelectionDialog, "exec", apply):
                        row.nests.click()

                def choose_one(dialog):
                    dialog.inherit.click()
                    dialog.checks[simulacrum].click()

                simulacrum = I18nText.SimulacrumNexusTacetDiscordNest
                stagnant = I18nText.StagnantRunTacetDiscordNest
                edit_nests(first, lambda dialog: dialog.select_all.click())
                edit_nests(second, choose_one)
                self.assertEqual(Config.load(path).daily.accountTacetDiscordNest["9892"], [simulacrum])
                edit_nests(second, lambda dialog: dialog.checks[stagnant].click())
                edit_nests(third, lambda dialog: dialog.skip.click())
                selected_nests = {"6663": "All", "9892": [simulacrum, stagnant], "917A": []}
                app.processEvents()

                event = Mock(source=lambda: third.handle, position=lambda: QPointF(15, first.geometry().top()))
                event.mimeData.return_value.hasFormat.return_value = True
                rows.dragEnterEvent(event)
                rows.dragMoveEvent(event)
                self.assertEqual(rows._drop_before, 0)
                rows.dragLeaveEvent(event)
                self.assertIsNone(rows._drop_before)
                self.assertEqual(rows._rows(), [first, second, third])
                rows.dropEvent(event)
                self.assertEqual(rows._rows(), [third, first, second])

                app.processEvents()
                event = Mock(source=lambda: first.handle, position=lambda: QPointF(15, second.geometry().bottom()))
                event.mimeData.return_value.hasFormat.return_value = True
                rows.dropEvent(event)
                self.assertEqual(rows._rows(), [third, second, first])
                event = Mock(source=lambda: first.handle, position=lambda: QPointF(-1, -1))
                event.mimeData.return_value.hasFormat.return_value = True
                rows.dropEvent(event)
                event.ignore.assert_called_once()
                self.assertEqual(rows._rows(), [third, second, first])

                snapshot = Config.load(path).daily
                self.assertEqual(parse_daily_account_suffixes(snapshot.accountSuffixes), ("917A", "9892", "6663"))
                self.assertEqual(snapshot.accountTacetSuppression, {"6663": stillness, "9892": flames})
                self.assertEqual(snapshot.accountTacetDiscordNest, selected_nests)
                self.assert_submission_order(path, snapshot)
                paramConfig.load(path)
                reopened = DailyWidget()
                self.addCleanup(reopened.close)
                editor = reopened.dailyAccountRows
                self.assertEqual(editor.configuration(require_minimum=True),
                                 ("917A, 9892, 6663", {"6663": stillness, "9892": flames}, selected_nests))
                self.assertEqual([row.nests.text() for row in editor._rows()],
                                 ["聚落：跳过", "聚落：已选 2 个", "聚落：全选"])
                edit_nests(editor._rows()[0], lambda dialog: dialog.inherit.click())
                self.assertNotIn("917A", Config.load(path).daily.accountTacetDiscordNest)
                row = editor._rows()[0]
                row.suffix.setText("9892")
                with self.assertRaisesRegex(ValueError, "9892"):
                    editor.configuration(require_minimum=True)
                row.suffix.setText("")
                row.dungeon.setCurrentIndex(1)
                with self.assertRaises(ValueError):
                    editor.configuration(require_minimum=True)
                row.suffix.setText("0063")
                row.dungeon.setCurrentIndex(0)
                self.assertTrue(editor.configuration(require_minimum=True)[0].startswith("0063"))
                row.remove.click()
                self.assertEqual(Config.load(path).daily.accountSuffixes, "9892, 6663")
                self.assertEqual(Config.load(path).daily.accountTacetDiscordNest,
                                 {"6663": "All", "9892": [simulacrum, stagnant]})

    def assert_submission_order(self, path, saved):
        from src import application
        from src.controller.main_controller import MainController
        from src.util import file_util

        submissions = []

        def build_task(*, args, **options):
            event, spec, _ = args

            def start():
                config = Config.from_dict(spec.user_config).daily
                submissions.append((parse_daily_account_suffixes(config.accountSuffixes),
                                    config.accountTacetSuppression, config.accountTacetDiscordNest))
                event.clear()

            return Mock(start=start)

        monitor = Mock(game_path="game.exe", launch_info=None)
        with (
            patch.object(file_util, "get_temp_config", return_value=path),
            patch("src.controller.main_controller.ProcessBridge"),
            patch("src.controller.main_controller.TaskMonitor", return_value=monitor),
            patch("src.core.tasks.DailyTask.build", side_effect=build_task),
        ):
            self.assertTrue(MainController().execute("DailyTask", "START")[0])
            with patch.object(application, "before"), patch("sys.argv", ["main.py", "--task", "DailyTask"]):
                application.run()
        self.assertEqual(submissions, [(("917A", "9892", "6663"), saved.accountTacetSuppression,
                                        saved.accountTacetDiscordNest)] * 2)
