"""Small helpers used by the vendored character routines."""

import logging

import cv2
import numpy as np


class Logger:
    get_logger = staticmethod(logging.getLogger)


text_white_color = {channel: (244, 255) for channel in "rgb"}


def color_range_to_bound(color_range):
    return tuple(np.array([color_range[c][i] for c in "bgr"], dtype=np.uint8) for i in (0, 1))


def binarize_for_matching(image, threshold=244):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)[1]


def isolate_white_text_to_black(image):
    mask = cv2.inRange(image, (0, 0, 0), (243, 243, 243))
    return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


def isolate_gua_strokes(image):
    gray = image.min(axis=2) if image.ndim == 3 else image
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
