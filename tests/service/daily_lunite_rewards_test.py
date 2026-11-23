import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.core.geometry import TextBox
from src.core.i18n import I18nText, I18nTr, Language
from src.core.pages import GlobalPage, OcrResult, _LuniteRewardVision
from src.service import daily_workflow
from src.service.saved_accounts import SavedAccountSwitcher


class LuniteRewardsTest(unittest.TestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(tr=I18nTr(Language.ZH), ocr_service=Mock())
        self.ui = Mock()
        self.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.ui.grap.return_value = self.frame
        self.ui.img = self.frame
        self.ui.activate.return_value = self.ui
        self.ui.sleep.return_value = self.ui
        self.ui.snapshot.return_value = self.ui
        self.claim = TextBox(500, 620, 760, 650, "点击领取 今日月相观测卡奖励", 1)
        self.items = TextBox(610, 190, 670, 210, "获得", 1)
        self.close = TextBox(540, 620, 740, 650, "点击空白处关闭", 1)
        self.claim_frame = OcrResult([self.claim])
        self.items_frame = OcrResult([self.items, self.close])

    def test_account_entry_waits_until_reward_and_confirmation_are_closed(self):
        switcher = SavedAccountSwitcher(self.ctx)
        switcher.ui = self.ui
        self.ui.is_on_homepage.return_value = True
        self.ctx.ocr_service.query.side_effect = [self.claim_frame, self.items_frame, OcrResult([])]

        self.assertFalse(switcher._enter_world())
        self.assertFalse(switcher._enter_world())
        self.ui.is_on_homepage.assert_not_called()
        self.assertTrue(switcher._enter_world())
        self.ui.click.assert_called_once_with(*self.claim.center, delay=0.2)
        self.ui.click_bbox.assert_called_once_with(self.close, delay=0.2)

    def test_daily_dispatcher_handles_reward_before_using_terminal(self):
        tr = self.ctx.tr
        terminal = [TextBox(5, 5, 50, 25, tr(key).raw, 1) for key in (
            I18nText.Events, I18nText.SOL3Phase, I18nText.UnionLevel, I18nText.UnionEXP,
        )]
        with patch.object(daily_workflow, "UIOp", return_value=self.ui):
            self.ui.search.side_effect = OcrResult([*terminal, self.claim]).search
            self.assertIsNone(daily_workflow.globalDispatcher(self.ctx, None))
            self.ui.search.side_effect = self.items_frame.search
            self.assertIsNone(daily_workflow.globalDispatcher(self.ctx, None))
            self.ui.search.side_effect = OcrResult(terminal).search
            self.assertEqual(daily_workflow.globalDispatcher(self.ctx, None), I18nText.Terminal)
        self.ui.click.assert_called_once_with(*self.claim.center, delay=0.2)
        self.ui.click_bbox.assert_called_once_with(self.close, delay=0.2)
        self.ui.esc.assert_not_called()

    def test_template_fallback_claims_only_when_card_is_visible(self):
        frame = self.frame.copy()
        feature = _LuniteRewardVision(frame).get_feature_by_name("monthly_card")
        feature.box.crop_frame(frame)[:] = feature.mat
        pages = GlobalPage(self.ctx)
        self.assertFalse(pages.handle_login_rewards(ui=self.ui, frame=self.frame, ocr=OcrResult([])))
        self.ui.click.assert_not_called()
        self.assertTrue(pages.handle_login_rewards(ui=self.ui, frame=frame, ocr=OcrResult([])))
        self.ui.click.assert_called_once_with(640, 641, delay=0.2)

    def test_unrelated_close_prompt_does_not_consume_input(self):
        pages = GlobalPage(self.ctx)
        self.assertFalse(pages.handle_login_rewards(ui=self.ui, frame=self.frame, ocr=OcrResult([self.close])))
        self.ui.click.assert_not_called()
        self.ui.click_bbox.assert_not_called()
