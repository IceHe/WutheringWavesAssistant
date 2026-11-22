import json
import os
import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import Mock, patch

from src.gui.common.daily_schedule import DailySchedule


def local_time(day, hour, minute, second=0):
    return datetime(2026, 11, day, hour, minute, second)


class DailyScheduleTest(unittest.TestCase):
    def test_defaults_to_inactive_until_configured(self):
        submit = Mock(return_value=True)
        schedule = DailySchedule()
        self.assertIsNone(schedule.poll(local_time(23, 4, 5), "", submit))
        submit.assert_not_called()

    def test_defers_until_idle_and_runs_once_per_day(self):
        submit = Mock(side_effect=[False, True, True])
        schedule = DailySchedule()
        schedule.configure(local_time(23, 4, 4), time(4, 5))
        self.assertIsNone(schedule.poll(local_time(23, 4, 4, 59), "", submit))
        submit.assert_not_called()
        self.assertIsNone(schedule.poll(local_time(23, 4, 5), "", submit))
        self.assertEqual(schedule.poll(local_time(23, 4, 20), "", submit), "2026-11-23")
        self.assertIsNone(schedule.poll(local_time(23, 23, 59), "2026-11-23", submit))
        self.assertEqual(schedule.poll(local_time(24, 4, 5), "2026-11-23", submit), "2026-11-24")
        self.assertEqual(submit.call_count, 3)

    def test_restart_during_scheduled_minute_uses_persisted_date(self):
        submit = Mock(return_value=True)
        schedule = DailySchedule()
        schedule.configure(local_time(23, 4, 5, 12), time(4, 5))
        self.assertIsNone(schedule.poll(local_time(23, 4, 5, 20), "2026-11-23", submit))
        submit.assert_not_called()
        schedule.configure(local_time(23, 4, 5, 25), time(4, 5))
        self.assertEqual(schedule.poll(local_time(23, 4, 5, 30), "", submit), "2026-11-23")

    def test_opening_after_the_scheduled_minute_waits_until_tomorrow(self):
        submit = Mock(return_value=True)
        schedule = DailySchedule()
        schedule.configure(local_time(23, 10, 0), time(4, 5))
        self.assertIsNone(schedule.poll(local_time(23, 10, 1), "", submit))
        submit.assert_not_called()
        self.assertEqual(schedule.poll(local_time(24, 4, 5), "", submit), "2026-11-24")

    def test_resume_uses_todays_occurrence_and_disabling_cancels_it(self):
        submit = Mock(return_value=True)
        schedule = DailySchedule()
        schedule.configure(local_time(23, 2, 0), time(4, 5))
        self.assertEqual(schedule.poll(local_time(24, 8, 0), "", submit), "2026-11-24")
        schedule.configure(local_time(24, 8, 1), None)
        self.assertIsNone(schedule.poll(local_time(25, 4, 5), "2026-11-24", submit))
        submit.assert_called_once()


class DailyScheduleGuiTest(unittest.TestCase):
    def test_persists_settings_and_submits_daily_from_another_tab_when_idle(self):
        from PySide6.QtCore import QTime, QTranslator
        from PySide6.QtWidgets import QApplication

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication([])
        from src.gui.common.config import paramConfig
        from src.gui.common.globals import globalSignal
        from src.gui.view.home.daily import DailyWidget
        from src.gui.view.home_interface import HomeV2Interface

        translator = QTranslator()
        translations = Path(__file__).resolve().parents[2] / "src/gui/resource/i18n/gallery.zh_CN.qm"
        self.assertTrue(translator.load(str(translations)))
        app.installTranslator(translator)
        self.addCleanup(app.removeTranslator, translator)

        items = (paramConfig.dailyScheduleEnabled, paramConfig.dailyScheduleTime,
                 paramConfig.dailyScheduleLastDate, paramConfig.dailyMultiAccount)
        for item in items:
            self.addCleanup(paramConfig.set, item, paramConfig.get(item), False)

        active_tasks = Mock(return_value=frozenset({"ExploreTask"}))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "param-config.json"
            with (patch.object(paramConfig._cfg, "file", path),
                  patch("src.gui.view.home_interface.datetime", wraps=datetime) as clock):
                for item in items:
                    paramConfig.set(item, item.defaultValue, save=False)
                clock.now.return_value = local_time(23, 6, 29)
                home = HomeV2Interface()
                home.enableDailyScheduling(active_tasks)
                self.addCleanup(home.close)
                checkbox = home.contentWidget.daily.dailyScheduleCheckBox
                editor = home.contentWidget.daily.dailyScheduleTimeEdit
                self.assertEqual(checkbox.text(), "定时开始日常")
                self.assertEqual(editor.time(), QTime(4, 5))
                self.assertFalse(editor.isEnabled())
                self.assertFalse(home._daily_schedule_timer.isActive())
                checkbox.setChecked(True)
                editor.setTime(QTime(6, 30))
                self.assertTrue(home._daily_schedule_timer.isActive())
                self.assertTrue(editor.isEnabled())
                home.bottomWidget.currentTask = home.contentWidget.explore.currentTask

                submitted = Mock()
                globalSignal.executeTaskSignal.connect(submitted)
                try:
                    clock.now.return_value = local_time(23, 6, 30)
                    home._pollDailySchedule()
                    submitted.assert_not_called()
                    active_tasks.return_value = frozenset()
                    clock.now.return_value = local_time(23, 6, 42)
                    home._pollDailySchedule()
                    home._pollDailySchedule()
                    submitted.assert_called_once_with("DailyTask", "START")
                    self.assertIs(home.bottomWidget._submittedTask, home.contentWidget.daily.task)
                finally:
                    globalSignal.executeTaskSignal.disconnect(submitted)

                saved = json.loads(path.read_text(encoding="utf-8"))["Daily"]
                self.assertTrue(saved["ScheduleEnabled"])
                self.assertEqual(saved["ScheduleTime"], "06:30")
                self.assertEqual(saved["ScheduleLastDate"], "2026-11-23")
                paramConfig.load(path)
                reopened = DailyWidget()
                self.addCleanup(reopened.close)
                self.assertTrue(reopened.dailyScheduleCheckBox.isChecked())
                self.assertEqual(reopened.dailyScheduleTimeEdit.time(), QTime(6, 30))
                checkbox.setChecked(False)
                self.assertFalse(home._daily_schedule_timer.isActive())
