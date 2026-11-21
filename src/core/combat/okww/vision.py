"""Template and OCR operations against the frame supplied by WWA."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

import cv2
import numpy as np

from src.core.geometry import BBox

from .compat import binarize_for_matching, color_range_to_bound, isolate_gua_strokes

_TEMPLATE_NAME = re.compile(r"^(.+)_(\d+)x(\d+)_(\d+)_(\d+)$")
_TEMPLATE_ROOT = Path(__file__).with_name("templates")
_CENTERED = {"suisui_forte3"}


@dataclass
class Box:
    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0
    name: str | None = None

    def bounds(self, frame):
        left = max(0, self.x)
        top = max(0, self.y)
        right = min(frame.shape[1], self.x + self.width)
        bottom = min(frame.shape[0], self.y + self.height)
        return left, top, max(left, right), max(top, bottom)

    def crop_frame(self, frame):
        x, y, right, bottom = self.bounds(frame)
        return frame[y:bottom, x:right]

    def scale(self, width_ratio, height_ratio=None):
        width = round(self.width * width_ratio)
        height = round(self.height * (width_ratio if height_ratio is None else height_ratio))
        return Box(self.x + (self.width - width) // 2, self.y + (self.height - height) // 2,
                   width, height, self.confidence, self.name)


@dataclass(frozen=True)
class Feature:
    box: Box
    mat: np.ndarray


def _position(value, original, current, scale, centered):
    if centered:
        return round(current / 2 + (value - original / 2) * scale)
    if value > original / 2:
        return current - round((original - value) * scale)
    return round(value * scale)


def _resize(image, scale):
    if scale == 1:
        return image
    h, w = image.shape[:2]
    return cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


@lru_cache(maxsize=1)
def template_index():
    index = {}
    for path in sorted(_TEMPLATE_ROOT.glob("*.png")):
        name, width, height, x, y = _TEMPLATE_NAME.fullmatch(path.stem).groups()
        index.setdefault(name, []).append((path, int(width), int(height), int(x), int(y)))
    if not index:
        raise FileNotFoundError(f"OKWW combat templates missing from {_TEMPLATE_ROOT}")
    return index


@lru_cache(maxsize=512)
def _scaled_features(name, width, height):
    variants = template_index().get(name)
    if not variants:
        raise KeyError(f"OKWW combat template {name!r} is missing")
    features = []
    for path, base_w, base_h, x, y in variants:
        original = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if original is None:
            raise ValueError(f"Could not decode OKWW template {path.name}")
        factor = min(width / base_w, height / base_h)
        image = _resize(original, factor)
        if name == "mouse_forte":
            image = binarize_for_matching(image)
        elif name == "e_forte":
            image = binarize_for_matching(image, 220)
        elif name in {"douling_gen", "douling_zhen"}:
            image = isolate_gua_strokes(image)
        left = _position(x, base_w, width, factor, "hcenter" in name or name in _CENTERED)
        top = _position(y, base_h, height, factor, "vcenter" in name)
        features.append(Feature(Box(left, top, image.shape[1], image.shape[0], name=name), image))
    return tuple(features)


class Vision:
    @property
    def screen_width(self):
        return self.frame.shape[1]

    @property
    def screen_height(self):
        return self.frame.shape[0]

    def width_of_screen(self, fraction):
        return int(self.screen_width * fraction)

    def height_of_screen(self, fraction):
        return int(self.screen_height * fraction)

    def box_of_screen_scaled(self, original_screen_width, original_screen_height,
                             x_original, y_original, to_x=0, to_y=0, width_original=0,
                             height_original=0, name=None, hcenter=False, vcenter=False, confidence=1.0):
        width, height = self.screen_width, self.screen_height
        factor = min(width / original_screen_width, height / original_screen_height)
        x = _position(x_original, original_screen_width, width, factor, hcenter)
        y = _position(y_original, original_screen_height, height, factor, vcenter)
        w = round((width_original or to_x - x_original) * factor)
        h = round((height_original or to_y - y_original) * factor)
        return Box(x, y, w, h, confidence, name)

    def box_of_screen(self, x, y, to_x=1, to_y=1, *, hcenter=False, vcenter=False):
        return self.box_of_screen_scaled(3840, 2160, x * 3840, y * 2160, to_x * 3840, to_y * 2160,
                                         hcenter=hcenter, vcenter=vcenter)

    def get_feature_by_name(self, name):
        return _scaled_features(name, self.screen_width, self.screen_height)[0]

    def get_box_by_name(self, name):
        if isinstance(name, Box):
            return name
        box = self.get_feature_by_name(name).box
        return Box(box.x, box.y, box.width, box.height, name=box.name)

    def calculate_color_percentage(self, color, box):
        img = self.get_box_by_name(box).crop_frame(self.frame)
        if not img.size:
            return 0.0
        mask = cv2.inRange(img, *color_range_to_bound(color))
        return cv2.countNonZero(mask) / mask.size

    def find_one(self, feature_name=None, **kwargs):
        results = self.find_feature(feature_name, limit=1, **kwargs)
        return results[0] if results else None

    def find_best_match_in_box(self, box, names, threshold=0.8):
        results = [self.find_one(name, box=box, threshold=threshold) for name in names]
        return max((result for result in results if result), key=lambda result: result.confidence, default=None)

    def find_feature(self, feature_name=None, horizontal_variance=0.002, vertical_variance=0.002,
                     threshold=0.8, box=None, frame_processor=None, template=None,
                     target_height=0, limit=0, use_gray_scale=False, x=-1, y=-1, to_x=-1, to_y=-1):
        frame = self.frame
        height, width = frame.shape[:2]
        if isinstance(box, str):
            box = self.get_box_by_name(box)
        elif box is None and x >= 0 and y >= 0:
            box = Box(round(x * width), round(y * height),
                      round((to_x - x) * width), round((to_y - y) * height))
        features = ((Feature(Box(0, 0, width, height), template),) if template is not None
                    else _scaled_features(feature_name, width, height))
        matches = []
        for feature in features:
            region = box
            if region is None:
                fx, fy = max(1, round(width * horizontal_variance)), max(1, round(height * vertical_variance))
                b = feature.box
                region = Box(b.x - fx, b.y - fy, b.width + fx * 2, b.height + fy * 2)
            left, top, right, bottom = region.bounds(frame)
            area = frame[top:bottom, left:right]
            if not area.size:
                continue
            needle = feature.mat
            if frame_processor is not None:
                area = frame_processor(area)
            if use_gray_scale or needle.ndim == 2 or area.ndim == 2:
                if area.ndim == 3:
                    area = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
                if needle.ndim == 3:
                    needle = cv2.cvtColor(needle, cv2.COLOR_BGR2GRAY)
            scale = target_height / height if target_height else 1
            if area.shape[0] < needle.shape[0] or area.shape[1] < needle.shape[1]:
                continue
            original_h, original_w = needle.shape[:2]
            area, needle = _resize(area, scale), _resize(needle, scale)
            result = cv2.matchTemplate(area, needle, cv2.TM_CCOEFF_NORMED)
            nh, nw = needle.shape[:2]
            while True:
                _, score, _, (mx, my) = cv2.minMaxLoc(result)
                if not np.isfinite(score) or score < threshold:
                    break
                matches.append(Box(left + round(mx / scale), top + round(my / scale),
                                   original_w, original_h, score, feature_name))
                if limit == 1:
                    break
                result[max(0, my - nh + 1):my + nh, max(0, mx - nw + 1):mx + nw] = -1
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches[:limit] if limit else matches

    def ocr(self, x, y, to_x, to_y, *, frame_processor=None, match=None):
        left, top = self.width_of_screen(x), self.height_of_screen(y)
        right, bottom = self.width_of_screen(to_x), self.height_of_screen(to_y)
        roi = BBox(left, top, right, bottom)
        if frame_processor is None:
            frame = self.frame
        else:
            frame = self.frame.copy()
            frame[top:bottom, left:right] = frame_processor(frame[top:bottom, left:right])
        texts = self._ocr().query(frame, roi=roi).results or []
        return [Box(t.x1, t.y1, t.x2 - t.x1, t.y2 - t.y1, t.score or 0, t.text)
                for t in texts if match is None or match.search(t.text)]
