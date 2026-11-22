import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.core.exceptions import OcrInferenceError
from src.core.geometry import BBox
from src.service.ocr_service import RapidOcrServiceImpl


class OcrConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.empty_result = SimpleNamespace(boxes=None, scores=None, txts=None)
        self.engine = Mock(return_value=self.empty_result)
        with patch("src.service.ocr_service.rapidocr_util.create_ocr", return_value=self.engine):
            self.ocr = RapidOcrServiceImpl(SimpleNamespace(spec=None), Mock(), Mock())
        self.img = np.zeros((120, 160, 3), dtype=np.uint8)

    def test_full_frame_and_combat_roi_do_not_enter_the_same_engine_concurrently(self):
        full_frame_entered = threading.Event()
        resume_full_frame = threading.Event()
        roi_requested = threading.Event()
        roi_entered = threading.Event()

        def infer(img, **kwargs):
            if img.shape == self.img.shape:
                full_frame_entered.set()
                if not resume_full_frame.wait(3):
                    raise TimeoutError("Full-frame inference was not released")
            else:
                roi_entered.set()
            return self.empty_result

        def read_cooldown():
            roi_requested.set()
            return self.ocr.query(self.img, roi=BBox(100, 90, 152, 112))

        self.engine.side_effect = infer
        with ThreadPoolExecutor(max_workers=2) as worker:
            full_frame = worker.submit(self.ocr.query, self.img)
            try:
                self.assertTrue(full_frame_entered.wait(2))
                roi = worker.submit(read_cooldown)
                self.assertTrue(roi_requested.wait(2))
                self.assertFalse(roi_entered.wait(0.15))
            finally:
                resume_full_frame.set()
            full_frame.result(timeout=2)
            roi.result(timeout=2)
        self.assertTrue(roi_entered.is_set())

    def test_inference_error_preserves_cause_and_allows_another_query(self):
        error = RuntimeError("Tensor holds no memory")
        self.engine.side_effect = [error, self.empty_result]
        with self.assertRaises(OcrInferenceError) as raised:
            self.ocr.query(self.img)
        self.assertIs(raised.exception.__cause__, error)
        self.assertFalse(self.ocr._inference_lock.locked())
        self.assertEqual(self.ocr.query(self.img).results, [])
