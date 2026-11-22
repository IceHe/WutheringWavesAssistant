import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from src.core.exceptions import OcrInferenceError, StopError
from src.core.i18n import I18nText, I18nTr, Language
from src.core.task import LatchTaskFSM, TaskStatus
from src.service import daily_workflow


class DailyTacetNestTest(unittest.TestCase):
    def setUp(self):
        self.nest = LatchTaskFSM(name=I18nText.SouthernYuanHillsTacetDiscordNest)
        self.next_nest = LatchTaskFSM(name=I18nText.StarblindCrashsiteTacetDiscordNest)
        self.local = SimpleNamespace(members=[], tacetDiscordNestFSM=SimpleNamespace(
            is_terminal=False, children=[self.nest, self.next_nest]))
        self.ctx = SimpleNamespace(tr=I18nTr(Language.ZH), control_service=Mock(), img_service=Mock())
        self.ui = Mock()
        self.ui.wait.return_value.until.return_value = True
        self.ui.wait_back_home.return_value = True
        self.ui.sleep.return_value = self.ui
        self.ui.snapshot.return_value = self.ui
        self.ui.pick_up.return_value = self.ui
        self.ui.is_set.return_value = True
        self.ui.is_on_homepage.return_value = True
        self.combat = Mock()
        self.events = Mock()
        self.events.attach_mock(self.combat.start, "start")
        self.events.attach_mock(self.combat.stop, "stop")
        self.events.attach_mock(self.ctx.control_service.map, "map")
        self.absorb = Mock()

        tr = self.ctx.tr
        self.cleared_text = tr([
            I18nText.TacetDiscordNestCleared, I18nText.TacetDiscordNestClearedMengzhou, I18nText.RefreshesTomorrow])
        self.active_text = tr([I18nText.ClearTheTacetDiscordNest, I18nText.ClearTheTacetDiscordNestMengzhou])
        self.clear_after_start = None
        self.show_active_text = True
        self.ui.search.side_effect = self.search

        for name, result in (
            ("UIOp", self.ui), ("RoiEx", Mock()), ("Slider.points", [object()]),
            ("create_combat_system", self.combat), ("SIFTFeatureMatcher", Mock(feature_to_scene=Mock(return_value=(4, 8)))),
            ("img_util.read_img", object()), ("GlobalPage.action", None),
        ):
            self.enterContext(patch(f"src.service.daily_workflow.{name}", return_value=result))
        self.enterContext(patch("src.service.daily_workflow.AsyncPickup"))
        self.enterContext(patch("src.service.daily_workflow.absorb_around_variant_blind", self.absorb))

    def search(self, query, *args):
        tr = self.ctx.tr
        if query == tr(self.nest.name):
            return [SimpleNamespace(y1=3)]
        if query == tr([I18nText.Challenge, I18nText.Go]):
            return [SimpleNamespace(y1=4, y2=6)]
        if query == r"(\d{1,2}).*?(\d{1,2})":
            return [SimpleNamespace(text="24/41", y1=4, y2=6)]
        if query == tr([I18nText.FastTravel, I18nText.EnableNavigation, I18nText.Track]):
            return True
        if query == self.cleared_text:
            return self.clear_after_start is not None and self.combat.start.call_count >= self.clear_after_start
        if query == self.active_text:
            return self.show_active_text
        return None

    def run_nest(self):
        return daily_workflow.doTacetDiscordNest(self.ctx, self.local)

    def test_ocr_error_stops_combat_and_retries_on_site(self):
        self.clear_after_start = 2

        def snapshot():
            if self.ui.snapshot.call_count == 3:
                self.events.ocr_error()
                raise OcrInferenceError("Tensor holds no memory")
            if self.ui.snapshot.call_count == 4:
                self.events.ocr_retry()
            return self.ui

        self.ui.snapshot.side_effect = snapshot
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            self.assertTrue(self.run_nest())
        self.assertEqual(self.events.mock_calls, [
            call.start(3.5), call.ocr_error(), call.stop(join=True), call.ocr_retry(),
            call.start(3.5), call.stop(join=True), call.map(),
        ])
        self.assertEqual(self.nest.status, TaskStatus.COMPLETED)
        self.absorb.assert_called_once_with(self.ctx)
        self.assertEqual(self.next_nest.status, TaskStatus.PENDING)

    def test_repeated_ocr_error_preserves_current_nest_for_retry(self):
        self.ui.snapshot.side_effect = [self.ui, self.ui, OcrInferenceError(), OcrInferenceError()]
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            self.assertFalse(self.run_nest())
        self.assertEqual(self.events.mock_calls, [call.start(3.5), call.stop(join=True), call.stop(join=True)])
        self.assertEqual(self.nest.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(self.next_nest.status, TaskStatus.PENDING)
        self.absorb.assert_not_called()

    def test_cancellation_joins_combat_before_leaving_the_nest(self):
        self.ui.snapshot.side_effect = [self.ui, self.ui, StopError()]
        with self.assertRaises(StopError):
            self.run_nest()
        self.assertEqual(self.events.mock_calls, [call.start(3.5), call.stop(join=True)])
        self.absorb.assert_not_called()

    def test_exhausted_attempts_require_clear_confirmation(self):
        self.nest.count = 1
        self.show_active_text = False
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            self.assertFalse(self.run_nest())
        self.assertEqual(self.nest.status, TaskStatus.FAILED)
        self.assertEqual(self.next_nest.status, TaskStatus.PENDING)
        self.assertEqual(self.combat.stop.call_count, 3)
        self.absorb.assert_not_called()
