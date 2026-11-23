import json
import os
import subprocess
import tempfile
import threading
import unittest
from contextlib import nullcontext
from datetime import datetime, time
from pathlib import Path
from unittest.mock import Mock, call, patch

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


class DailyClientStartupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def create_installation(self, name):
        directory = self.root / name / "Wuthering Waves Game"
        binaries = directory / "Client" / "Binaries" / "Win64"
        binaries.mkdir(parents=True)
        (binaries / "Client-Win64-Shipping.exe").touch()
        executable = directory / "Wuthering Waves.exe"
        executable.touch()
        return executable

    def test_auto_cold_start_uses_current_domestic_registration(self):
        from src.config.config import Config
        from src.controller.main_controller import TaskMonitor
        from src.core.runtime import RuntimeConfig
        from src.util import hwnd_util, winreg_util

        stale = self.root / "Old install"
        stale.mkdir()
        (stale / "launcher.exe").touch()
        domestic = self.create_installation("Current install")
        overseas = self.create_installation("Overseas")
        registrations = {
            ("Aki_G152_default_10003", "SingleLauncherInstallPath"): domestic.parent.parent,
            ("KRInstall Wuthering Waves", "InstallPath"): stale,
            ("KRInstall Wuthering Waves Overseas", "InstallPath"): overseas.parent.parent,
        }

        def query_value(key, name):
            try:
                return str(registrations[key, name]), winreg_util.winreg.REG_SZ
            except KeyError:
                raise FileNotFoundError(name) from None

        with (
            patch.object(winreg_util.winreg, "OpenKey", side_effect=lambda hive, key: nullcontext(key.split("\\")[-1])),
            patch.object(winreg_util.winreg, "QueryValueEx", side_effect=query_value),
            patch.object(hwnd_util, "get_hwnds", return_value=[]),
        ):
            monitor = TaskMonitor.__new__(TaskMonitor)
            monitor.rt_cfg = RuntimeConfig(Config.from_dict({"Game": {"GamePath": "Auto"}}))
            self.assertEqual(monitor.get_launch_info().executable, str(domestic))
            registrations.pop(("Aki_G152_default_10003", "SingleLauncherInstallPath"))
            monitor.rt_cfg = RuntimeConfig(Config())
            self.assertEqual(monitor.get_launch_info().executable, str(overseas))
            registrations.pop(("KRInstall Wuthering Waves Overseas", "InstallPath"))
            monitor.rt_cfg = RuntimeConfig(Config())
            self.assertIsNone(monitor.get_launch_info().executable)

    def test_daily_starts_the_client_and_waits_for_its_window(self):
        from src.core.tasks import PrepareTask
        from src.core.workflow import LaunchInfo, TaskSpec
        from src.util import hwnd_util

        executable = self.create_installation("Install")
        launcher = executable.parent.parent / "launcher.exe"
        launcher.touch()
        event = threading.Event()
        event.set()
        spec = TaskSpec(game_path=str(launcher), launch_info=LaunchInfo(str(launcher), [str(launcher), "-krqlv=uhd"]))
        prepare = PrepareTask(event, spec, None)
        with (
            patch.object(hwnd_util, "get_hwnd", side_effect=[None, None, 42]) as game_window,
            patch("src.core.tasks.subprocess.Popen") as spawn,
            patch.object(prepare, "_sleep") as sleep,
        ):
            self.assertTrue(prepare.start_game())
        spawn.assert_called_once_with(
            [str(executable), "-krqlv=uhd"], cwd=executable.parent, creationflags=subprocess.DETACHED_PROCESS,
        )
        self.assertEqual(game_window.call_args_list[1:], [call(str(executable), force=True)] * 2)
        self.assertEqual(sleep.call_args_list, [call(2), call(2), call(10)])

    def test_launcher_only_installation_is_not_started(self):
        from src.core.tasks import PrepareTask
        from src.core.workflow import TaskSpec
        from src.util import hwnd_util

        launcher = self.root / "launcher.exe"
        launcher.touch()
        event = threading.Event()
        event.set()
        spec = TaskSpec(game_path=str(launcher))
        with (
            patch.object(hwnd_util, "get_hwnd", return_value=None),
            patch("src.core.tasks.subprocess.Popen") as spawn,
            self.assertLogs("src.core.tasks", "ERROR"),
        ):
            self.assertFalse(PrepareTask(event, spec, None).start_game())
        spawn.assert_not_called()


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
