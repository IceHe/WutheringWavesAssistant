import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from src.core.exceptions import OcrInferenceError, StopError
from src.core.geometry import BBox, TextBox
from src.core.i18n import I18nText, I18nTr, Language
from src.core.pages import OcrResult
from src.core.task import LatchTaskFSM, TaskFSMGroup, TaskStatus
from src.service import daily_workflow


class DailyTacetNestTest(unittest.TestCase):
    def setUp(self):
        self.nest = LatchTaskFSM(name=I18nText.SouthernYuanHillsTacetDiscordNest)
        self.next_nest = LatchTaskFSM(name=I18nText.StarblindCrashsiteTacetDiscordNest)
        self.local = SimpleNamespace(members=[], tacetDiscordNestFSM=TaskFSMGroup(self.nest, self.next_nest))
        self.ctx = SimpleNamespace(tr=I18nTr(Language.ZH), control_service=Mock(), img_service=Mock())
        self.ui = Mock()
        self.ui.wait.return_value.until.return_value = True
        self.ui.wait_back_home.return_value = True
        self.ui.sleep.return_value = self.ui
        self.ui.snapshot.return_value = self.ui
        self.ui.pick_up.return_value = self.ui
        self.ui.esc.return_value = self.ui
        self.ui.click_point.return_value = self.ui
        self.ui.is_set.return_value = True
        self.ui.is_on_homepage.return_value = True
        self.combat = Mock()
        self.events = Mock()
        self.events.attach_mock(self.combat.start, "start")
        self.events.attach_mock(self.combat.stop, "stop")
        self.events.attach_mock(self.ctx.control_service.map, "map")
        self.absorb = Mock()

        tr = self.ctx.tr
        self.content = BBox(454, 75, 1280, 660)
        self.menu = BBox(0, 0, 454, 720)
        self.title = TextBox(500, 100, 710, 120, tr(self.nest.name).raw, 1.0)
        self.button = TextBox(1100, 140, 1190, 160, tr(I18nText.Go).raw, 1.0)
        progress = TextBox(850, 170, 910, 190, "24/41", 1.0)
        self.cards = [self.title, self.button, progress]
        self.cleared_text = tr([
            I18nText.TacetDiscordNestCleared, I18nText.TacetDiscordNestClearedMengzhou, I18nText.RefreshesTomorrow])
        self.active_text = tr([I18nText.ClearTheTacetDiscordNest, I18nText.ClearTheTacetDiscordNestMengzhou])
        self.clear_after_start = None
        self.show_active_text = True
        self.ui.search.side_effect = self.search

        for name, result in (
            ("UIOp", self.ui), ("RoiEx", SimpleNamespace(guidebook_content=self.content, guidebook_menu=self.menu)),
            ("Slider.points", [object()]), ("create_combat_system", self.combat),
            ("SIFTFeatureMatcher", Mock(feature_to_scene=Mock(return_value=(4, 8)))),
            ("img_util.read_img", object()), ("GlobalPage.action", None),
        ):
            self.enterContext(patch(f"src.service.daily_workflow.{name}", return_value=result))
        self.enterContext(patch("src.service.daily_workflow.AsyncPickup"))
        self.enterContext(patch("src.service.daily_workflow.absorb_around_variant_blind", self.absorb))

    def search(self, query, *args):
        tr = self.ctx.tr
        if args and args[0] == self.content:
            return OcrResult(self.cards).search(query, self.content)
        if query == tr([I18nText.FastTravel, I18nText.EnableNavigation, I18nText.Track]):
            return True
        if query == self.cleared_text:
            return self.clear_after_start is not None and self.combat.start.call_count >= self.clear_after_start
        if query == self.active_text:
            return self.show_active_text
        return None

    def run_nest(self):
        return daily_workflow.doTacetDiscordNest(self.ctx, self.local)

    def test_missing_progress_enters_target_when_the_next_card_is_complete(self):
        self.clear_after_start = 0
        self.cards = [self.title, self.button,
                      TextBox(500, 240, 710, 260, self.ctx.tr(self.next_nest.name).raw, 1.0),
                      TextBox(850, 310, 910, 330, "48/48", 1.0)]
        self.assertTrue(self.run_nest())
        self.ui.click_bbox.assert_called_once_with(self.button)
        self.ui.click_text.assert_called_once_with(self.ctx.tr(I18nText.FastTravel), delay=0.2,
                                                  pk=daily_workflow.PointKind.NEAR, times=2, interval=0.3)
        self.assertEqual(self.nest.status, TaskStatus.COMPLETED)
        self.assertEqual(self.next_nest.status, TaskStatus.PENDING)

    def test_next_card_button_is_not_used_when_target_button_is_missing(self):
        self.cards = [self.title, TextBox(500, 240, 710, 260, self.ctx.tr(self.next_nest.name).raw, 1.0),
                      TextBox(1100, 280, 1190, 300, self.ctx.tr(I18nText.Go).raw, 1.0)]
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            self.assertFalse(self.run_nest())
        self.ui.click_bbox.assert_not_called()
        self.ui.click_point.assert_called_once()
        self.combat.start.assert_not_called()

    def test_completed_card_is_skipped_even_when_its_button_is_not_visible(self):
        self.local.tacetDiscordNestFSM.children = [self.nest]
        self.cards = [self.title, TextBox(850, 170, 910, 190, "41／41", 1.0)]
        self.assertTrue(self.run_nest())
        self.assertEqual(self.nest.status, TaskStatus.COMPLETED)
        self.ui.click_bbox.assert_not_called()
        self.combat.start.assert_not_called()

    def test_tab_click_is_confirmed_using_a_fresh_frame(self):
        self.local.tacetDiscordNestFSM.children = [self.nest]
        self.cards = [self.title, TextBox(850, 170, 910, 190, "41/41", 1.0)]
        self.ui.wait.return_value.until.side_effect = lambda check: check()
        switching = False
        list_visible = False

        def click_tab(pattern, roi):
            nonlocal switching
            self.assertEqual(pattern, self.ctx.tr(I18nText.TacetDiscordNest))
            self.assertEqual(roi, self.menu)
            switching = True
            return True

        def snapshot():
            nonlocal list_visible
            list_visible = switching
            return self.ui

        def search(pattern, *args):
            return self.search(pattern, *args) if list_visible else None

        self.ui.snapshot.side_effect = snapshot
        self.ui.click_text.side_effect = click_tab
        self.ui.search.side_effect = search
        self.assertTrue(self.run_nest())
        self.ui.click_text.assert_called_once()
        self.assertEqual(self.nest.status, TaskStatus.COMPLETED)

    def test_unconfirmed_destination_never_clicks_travel(self):
        # The list is already open. Both attempts to open the selected card time out.
        self.ui.wait.return_value.until.side_effect = [True, False, False]
        with self.assertLogs(daily_workflow.__name__, "WARNING"):
            self.assertFalse(self.run_nest())
        self.assertEqual(self.ui.click_bbox.call_args_list, [call(self.button), call(self.button)])
        self.ui.click_text.assert_not_called()
        self.combat.start.assert_not_called()
        self.assertEqual(self.nest.status, TaskStatus.IN_PROGRESS)

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
