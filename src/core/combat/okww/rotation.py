import logging
import re
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP

import cv2
import numpy as np

from .characters.BaseChar import BaseChar, SwitchPriority
from .compat import binarize_for_matching, color_range_to_bound, isolate_white_text_to_black, text_white_color

logger = logging.getLogger(__name__)
cd_regex = re.compile(r'\d{1,2}\.\d')


class NotInCombatException(Exception):
    """A character action could not continue on the current combat screen."""


class Rotation:
    CD_OCR_REFERENCE_ASPECT_RATIO = 16 / 9

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        """添加冻结持续时间。用于精确计算技能冷却等。

        Args:
            start (float): 冻结开始时间。
            duration (float, optional): 冻结持续时间。如果为-1.0, 则根据当前时间计算。默认为 -1.0。
            freeze_time (float, optional): 认为发生冻结的最小持续时间。默认为 0.1。
        """
        if duration < 0:
            duration = time.time() - start
        if start > 0 and duration > freeze_time:
            current_time = time.time()
            self.freeze_durations = [item for item in self.freeze_durations if item[0] > current_time - 60]
            self.freeze_durations.append((start, duration, freeze_time))

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        """计算扣除冻结时间后经过的时间。

        Args:
            start (float): 开始时间戳。
            intro_motion_freeze (bool, optional): 是否考虑角色入场动画的特殊冻结。默认为 False。

        Returns:
            float: 扣除冻结后实际经过的时间 (秒)。
        """
        if start < 0:
            return 10000
        to_minus = 0
        for freeze_start, duration, freeze_time in self.freeze_durations:
            if start < freeze_start:
                if intro_motion_freeze:
                    if freeze_time == -100:
                        freeze_time = 0
                elif freeze_time == -100:
                    continue
                to_minus += duration - freeze_time
        if to_minus != 0:
            self.log_debug(f'time_elapsed_accounting_for_freeze to_minus {to_minus}')
        return time.time() - start - to_minus

    def refresh_cd(self):
        current = self.get_current_char()
        if current is None or not self.in_team()[0]:
            return
        index = current.index
        previous = self.cds.get(index)
        if previous and time.time() - previous['time'] < 0.35:
            return
        cds = self.cds.get(index)
        if cds is None:
            cds = {}
            self.cds[index] = cds
        cds['time'] = time.time()
        cds['resonance'] = 0
        cds['liberation'] = 0
        cds['echo'] = 0
        cd_x = self._cd_ocr_relative_x
        texts = self.ocr(cd_x(0.82), 0.86, cd_x(0.97), 0.93,
                         frame_processor=isolate_white_text_to_black, match=cd_regex)
        for text in texts:
            cd = convert_cd(text)
            if text.x < self.width_of_screen(cd_x(0.86)):
                cds['resonance'] = cd
            elif text.x > self.width_of_screen(cd_x(0.91)):
                cds['liberation'] = cd
            else:
                cds['echo'] = cd
        self.log_debug(f'cd refreshed: {cds} {time.time() - cds["time"]}')

    def _cd_ocr_relative_x(self, x):
        """Translate a 16:9 HUD x-coordinate to the current screen width.

        The skill row keeps its size relative to screen height, so ultrawide
        displays add horizontal space outside the 16:9 HUD layout.
        """
        screen_width = self.width_of_screen(1)
        reference_width = min(screen_width, self.height_of_screen(1) * self.CD_OCR_REFERENCE_ASPECT_RATIO)
        return 1 - (1 - x) * reference_width / screen_width

    def get_cd(self, box_name, char_index=None):
        self.refresh_cd()
        if char_index is None:
            current = self.get_current_char()
            if current is None:
                return 0
            char_index = current.index
        if cds := self.cds.get(char_index):
            time_elapsed = self.time_elapsed_accounting_for_freeze(cds['time'])
            return max(0, cds.get(box_name, 0) - time_elapsed)
        else:
            return 0

    def available(self, name, check_color=True, check_cd=True):
        """检查指定名称的技能或动作是否可用 (通过颜色百分比和冷却时间判断)。

        Args:
            name (str): 技能或动作的名称 (例如 'resonance', 'echo')。

        Returns:
            bool: 如果可用则返回 True, 否则 False。
        """
        if check_color:
            current = self.box_highlighted(name)
        else:
            current = 1
        if current > 0 and (not check_cd or not self.has_cd(name)):
            return True

    def box_highlighted(self, name):
        current = self.calculate_color_percentage(text_white_color,
                                                  self.get_box_by_name(f'box_{name}'))
        if current > 0:
            current = 1
        else:
            current = 0
        return current

    def _oldest_switch_target(self, chars):
        chars = [char for char in chars if char is not None]
        if not chars:
            return None
        return min(chars, key=lambda char: (char.last_switch_in_time, char.index))

    def _unbuffed_support_target(self, candidates, allow_healer=True):
        role_order = ('is_healer', 'is_sub_dps') if allow_healer else ('is_sub_dps',)
        for role in role_order:
            target = self._oldest_switch_target([
                char for char in candidates
                if getattr(char, role) and char.buff_time > 0 and not char.has_buff()
            ])
            if target:
                return target
        return None

    def _switch_rule_3_target(self, candidates, allow_healer=True):
        unbuffed_support = self._unbuffed_support_target(candidates, allow_healer)
        if unbuffed_support:
            return unbuffed_support

        main_dps = [char for char in candidates if char.is_main_dps]
        if main_dps:
            return self._oldest_switch_target(main_dps)

        return self._oldest_switch_target(candidates)

    def _target_has_switch_cd(self, char):
        return char.time_elapsed_accounting_for_freeze(char.last_switch_time) <= 1

    def _log_switch_candidate(self, char, priority):
        buff_active = char.has_buff()
        buff_remaining = self._buff_remaining(char) if buff_active else 0
        switch_cd = char.last_switch_time >= 0 and self._target_has_switch_cd(char)
        logger.debug(
            f'switch selection candidate={char} index={char.index} role={char.char_type} '
            f'priority={priority} buff_active={buff_active} buff_remaining={buff_remaining:.2f} '
            f'buff_time={char.buff_time} last_buff_time={char.last_buff_time:.3f} '
            f'switch_cd={switch_cd} last_switch_time={char.last_switch_time:.3f}')

    def _log_switch_choice(self, current_char, target, has_intro, reason):
        logger.info(
            f'switch selection result current={current_char}({current_char.char_type}) '
            f'target={target}({target.char_type}) has_intro={has_intro} reason={reason}')
        return target

    def _buff_remaining(self, char):
        if char.buff_time <= 0 or not char.has_buff():
            return 0
        return max(0, char.buff_time - char.time_elapsed_accounting_for_freeze(char.last_buff_time))

    def _lowest_buff_remaining_target(self, candidates):
        buffers = [char for char in candidates if not char.is_main_dps and char.buff_time > 0]
        if not buffers:
            return None
        return min(buffers, key=lambda char: (self._buff_remaining(char), char.last_switch_in_time, char.index))

    def _unbuffed_non_main_target(self, current_char, candidates):
        if current_char.is_main_dps or current_char.buff_time <= 0:
            return None
        unbuffed_non_main = [
            char for char in candidates
            if not char.is_main_dps and char.buff_time > 0
               and not char.has_buff()
        ]
        return self._oldest_switch_target(unbuffed_non_main)

    def _choose_intro_switch_target(self, candidates):
        unbuffed_support = self._unbuffed_support_target(candidates)
        if unbuffed_support:
            return unbuffed_support

        role_order = ('is_main_dps', 'is_sub_dps', 'is_healer')
        for char_type in role_order:
            target = self._oldest_switch_target([char for char in candidates if getattr(char, char_type)])
            if target:
                return target
        return None

    def _choose_switch_target_by_buff_time(self, current_char, candidates):
        if not candidates:
            return current_char

        if current_char.is_main_dps:
            lowest_buff_remaining = self._lowest_buff_remaining_target(candidates)
            if lowest_buff_remaining:
                return lowest_buff_remaining

        unbuffed_non_main = self._unbuffed_non_main_target(current_char, candidates)
        if unbuffed_non_main:
            return unbuffed_non_main

        if current_char.is_sub_dps or current_char.is_healer:
            main_dps = [char for char in candidates if char.is_main_dps]
            if main_dps:
                return self._oldest_switch_target(main_dps)

        return self._switch_rule_3_target(candidates)

    def _choose_switch_target(self, current_char, has_intro, target_low_con=False):
        candidates = [
            char for char in self.chars
            if char is not None and char != current_char and self.is_slot_alive(char.index)
        ]
        if not candidates:
            return current_char

        prioritized_candidates = []
        locked_healers = []
        for char in candidates:
            switch_priority = char.get_switch_priority(current_char=current_char, has_intro=has_intro,
                                                       target_low_con=target_low_con)
            if char.healer_full_con_switch_locked():
                if switch_priority > SwitchPriority.NO:
                    locked_healers.append(char)
                switch_priority = SwitchPriority.NO
            self._log_switch_candidate(char, switch_priority)
            if switch_priority > SwitchPriority.NO:
                prioritized_candidates.append((switch_priority, char))

        if not prioritized_candidates:
            if locked_healers and (not current_char.is_healer or has_intro):
                target = min(locked_healers, key=lambda char: (
                    char.HEALER_FULL_CON_SWITCH_LOCKOUT
                    - char.time_elapsed_accounting_for_freeze(char.last_full_con_switch_time),
                    char.last_switch_in_time, char.index))
                return self._log_switch_choice(current_char, target, has_intro, 'healer_lockout_fallback')
            return self._log_switch_choice(
                current_char, current_char, has_intro, 'no_candidate_above_no_priority')

        highest_priority = max(priority for priority, _ in prioritized_candidates)
        candidates = [char for priority, char in prioritized_candidates if priority == highest_priority]

        if has_intro:
            if highest_priority >= SwitchPriority.MUST:
                target = self._oldest_switch_target(candidates) or current_char
                return self._log_switch_choice(
                    current_char, target, has_intro, f'priority_{highest_priority}')
            target = self._choose_intro_switch_target(candidates) or current_char
            if target != current_char and not target.is_main_dps and not target.has_buff():
                reason = f'intro_unbuffed_{target.char_type.value.lower()}'
            else:
                reason = 'intro_role_order_main_sub_healer'
            return self._log_switch_choice(current_char, target, has_intro, reason)

        candidates_without_switch_cd = [char for char in candidates if not self._target_has_switch_cd(char)]
        if candidates_without_switch_cd:
            candidates = candidates_without_switch_cd

        target = self._choose_switch_target_by_buff_time(current_char, candidates)
        if target != current_char and not target.is_main_dps and not target.has_buff():
            reason = f'unbuffed_{target.char_type.value.lower()}'
        elif current_char.is_main_dps and not target.is_main_dps:
            reason = 'lowest_support_buff_remaining'
        elif not current_char.is_main_dps and target.is_main_dps:
            reason = 'support_buffs_active_return_to_main_dps'
        else:
            reason = 'fallback_role_order'
        return self._log_switch_choice(current_char, target, has_intro, reason)

    def _apply_intro_flags(self, current_char, switch_to, has_intro):
        switch_to.has_intro = has_intro
        switch_to.has_sub_dps_intro = has_intro and current_char.is_sub_dps

    def switch_next_char(self, current_char, post_action=None, free_intro=False, target_low_con=False):
        """切换到下一个最优角色。

        Args:
            current_char (BaseChar): 当前角色对象。
            post_action (callable, optional): 切换后执行的动作 (回调函数)。默认为 None。
            free_intro (bool, optional): 是否强制认为拥有入场技 (通常在协奏值满时)。默认为 False。
            target_low_con (bool, optional): 是否优先切换到协奏值较低的角色。默认为 False。
        """
        has_intro = free_intro
        current_con = 0
        self.update_lib_portrait_icon()
        if not has_intro:
            current_con = current_char.get_current_con()
            if current_con > 0.8 and current_con != 1:
                logger.info(f'switch_next_char current_con {current_con:.2f} almost full, sleep and check again')
                self.sleep(0.05)
                self.next_frame()
                current_con = current_char.get_current_con()
            if current_con == 1:
                has_intro = True

        switch_to = self._choose_switch_target(current_char, has_intro, target_low_con=target_low_con)
        if not switch_to or switch_to == current_char:
            logger.warning(f"{current_char} can't find next char to switch to, performing too fast add a normal attack")
            current_char.continues_normal_attack(0.2)
            return
        self._apply_intro_flags(current_char, switch_to, has_intro)
        logger.info(
            f'switch_next_char {current_char}({current_char.char_type}) -> {switch_to}({switch_to.char_type}) '
            f'has_intro {switch_to.has_intro} has_sub_dps_intro {switch_to.has_sub_dps_intro} '
            f'current_con {current_con}')
        # if self.debug:
        #     self.screenshot(f'switch_next_char_{current_con}')
        from .characters.ShoreKeeper import ShoreKeeper
        last_click = 0
        start = time.time()
        while True:
            if not (isinstance(switch_to, ShoreKeeper) and has_intro):
                self.check_combat()
            now = time.time()
            if now - start > 10:
                self.raise_not_in_combat('failed switch chars')
            _, current_index, _ = self.in_team()
            if current_index == current_char.index:
                self.update_lib_portrait_icon()
                refreshed_has_intro = has_intro or current_char.is_con_full()
                if refreshed_has_intro != has_intro:
                    has_intro = refreshed_has_intro
                    switch_to = self._choose_switch_target(current_char, has_intro,
                                                           target_low_con=target_low_con)
                    if not switch_to or switch_to == current_char:
                        logger.warning(
                            f"{current_char} can't find next char to switch to after intro refresh, "
                            f"performing too fast add a normal attack")
                        current_char.continues_normal_attack(0.2)
                        return
                    logger.info(f'switch_next_char refreshed target after intro became available: {switch_to}')
                self._apply_intro_flags(current_char, switch_to, has_intro)
                if has_intro:
                    current_char.f_break(check_f_on_switch=True)
            if switch_to.wait_switch():
                self.click()
                self.sleep(0.1)
                continue
            if now - last_click > 0.1:
                self.send_key(switch_to.index + 1)
                self.sleep(0.001)
                last_click = now
                self.log_debug('switch not detected, send click')
                self.click()
                self.sleep(0.001)
            in_team, current_index, size = self.in_team()
            if not in_team:
                logger.info(f'not in team while switching chars_{current_char}_to_{switch_to} {now - start}')
                # if self.debug:
                #     self.screenshot(f'not in team while switching chars_{current_char}_to_{switch_to} {now - start}')
                # Intro animations can briefly hide the whole party HUD.
                # Wait for it to return before deciding that combat ended.
                if now - start > 5:
                    self.raise_not_in_combat(
                        f'switch too long failed chars_{current_char}_to_{switch_to}, {now - start}')
                self.next_frame()
                continue
            if current_index == switch_to.index:
                self.in_liberation = False
                if not has_intro:
                    current_char.f_break(check_f_on_switch=True)
                current_char.switch_out(con_full=has_intro)
                switch_to.is_current_char = True
                switch_to.last_switch_in_time = time.time()
                if has_intro:
                    current_time = time.time()
                    self.add_freeze_duration(current_time, switch_to.intro_motion_freeze_duration, -100)
                    current_char.last_outro_time = current_time
                break
            self.next_frame()

        if post_action:
            logger.debug(f'post_action {post_action}')
            post_action(switch_to, has_intro)
        logger.info(f'switch_next_char end {(current_char.last_switch_time - start):.3f}s')

    def find_mouse_forte(self):
        return self.find_one('mouse_forte', horizontal_variance=0.025, vertical_variance=0.015, threshold=0.6,
                             frame_processor=binarize_for_matching)

    def find_e_forte(self):
        return self.find_one('e_forte', horizontal_variance=0.025, threshold=0.6,
                             frame_processor=lambda img: binarize_for_matching(img, 220))

    def get_liberation_key(self):
        """获取共鸣解放技能的按键。

        Returns:
            str: 共鸣解放技能的按键字符串。
        """
        return self.key_config['Liberation Key']

    def get_echo_key(self):
        """获取声骸技能的按键。

        Returns:
            str: 声骸技能的按键字符串。
        """
        return self.key_config['Echo Key']

    def get_resonance_key(self):
        """获取共鸣技能的按键。

        Returns:
            str: 共鸣技能的按键字符串。
        """
        return self.key_config['Resonance Key']

    def has_cd(self, box_name, char_index=None):
        """检查指定UI区域是否处于冷却状态 (通过检测特定颜色的点和数字)。

        Args:
            box_name (str): UI区域的名称 (例如 'resonance', 'echo', 'liberation')。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.get_cd(box_name, char_index) > 0.2

    def get_current_char(self, raise_exception=False) -> BaseChar:
        """获取当前操作的角色对象。

        Args:
            raise_exception (bool, optional): 如果找不到当前角色是否抛出异常。默认为 True。

        Returns:
            BaseChar: 当前角色对象 (`BaseChar`) 或 None。
        """
        for char in self.chars:
            if char and char.is_current_char:
                return char
        if raise_exception and not self.in_team()[0]:
            self.raise_not_in_combat('can find current char!!')
        # self.load_chars()
        return None

    def has_short_action(self):
        """是否有短动作条"""
        return self.find_one(self.get_target_names()[0], box='target_box_short', threshold=0.6)

    def has_char(self, char_cls):
        for char in self.chars:
            if isinstance(char, char_cls):
                return char

    def is_con_full(self):
        """检查当前角色的协奏值是否已满。

        Returns:
            bool: 如果协奏值已满则返回 True, 否则 False。
        """
        return self.get_current_con() == 1

    def _ensure_ring_index(self):
        """确保当前角色协奏值环的颜色索引已识别。

        Returns:
            int: 协奏值环的颜色索引。
        """
        current = self.get_current_char()
        if current is None:
            return -1
        if current.ring_index < 0:
            box = self.get_con_box()

            best_index = -1
            best_percentage = 0
            for i in range(len(con_colors)):
                percent = self.calculate_color_percentage(con_colors[i], box)
                if percent > best_percentage:
                    best_percentage = percent
                    best_index = i
            current.ring_index = best_index
            self.log_debug(
                f'_ensure_ring_index {current} to {current.ring_index}')
        return current.ring_index

    def get_con_box(self):
        """获取协奏值能量环的UI区域盒子对象。

        Returns:
            Box: 盒子对象。
        """
        return self.box_of_screen_scaled(3840, 2160, 1431, 1942, 1557, 2068, name='con_full',
                                         hcenter=True)

    def get_current_con(self):
        """获取当前角色的协奏值百分比。

        Returns:
            float: 协奏值百分比 (0.0 到 1.0)。
        """
        box = self.get_con_box()
        box.confidence = 0

        max_area = 0
        percent = 0
        max_is_full = False
        target_index = self._ensure_ring_index()

        cropped = box.crop_frame(self.frame)
        if cropped.size == 0 or self.get_current_char() is None:
            return 0
        for i in range(len(con_colors)):
            if target_index != -1 and i != target_index:
                continue
            color_range = con_colors[i]
            area, is_full = self.count_rings(cropped, color_range,
                                             1500 / 3840 / 2160 * self.screen_width * self.screen_height)
            if is_full:
                max_is_full = is_full
            if area > max_area:
                max_area = int(area)
        if max_is_full:
            percent = 1
        if max_is_full and target_index >= 0:
            self.con_full_size[str(target_index)] = max_area

        full_size = self.con_full_size.get(str(target_index), 0)
        if percent != 1 and full_size > 0:
            percent = max_area / full_size
        if not max_is_full and percent >= 1:
            self.logger.warning(
                f'is_con_full not full but percent greater than 1, set to 0.99, {percent} {max_is_full}')
            percent = 0.99
        if percent > 1:
            self.logger.error(f'is_con_full percent greater than 1, set to 1, {percent} {max_is_full}')
            percent = 1

        box.confidence = percent
        self.draw_boxes(f'is_con_full_{self}', box)
        if percent > 1:
            percent = 1
        return percent

    def count_rings(self, image, color_range, min_area):
        """在指定图像区域内计算特定颜色范围的能量环数量和状态。

        Args:
            image (numpy.ndarray): 要分析的图像 (通常是协奏值UI区域的截图)。
            color_range (dict): 目标颜色范围。
            min_area (float): 认为是有效能量环的最小面积。

        Returns:
            tuple: (检测到的区域面积 (int), 是否为完整环 (bool))。
        """
        # Define the color range
        lower_bound, upper_bound = color_range_to_bound(color_range)
        masked_image = image.copy()
        h, w = image.shape[:2]
        center = (w // 2, h // 2)

        # draw mask
        r1, r2 = h * 0.35119, h * 0.42261
        r1 = Decimal(str(r1)).quantize(Decimal('0'), rounding=ROUND_DOWN)
        r2 = Decimal(str(r2)).quantize(Decimal('0'), rounding=ROUND_UP)

        ring_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(ring_mask, center, int(r2), 255, -1)
        cv2.circle(ring_mask, center, int(r1), 0, -1)
        masked_image = cv2.bitwise_and(masked_image, masked_image, mask=ring_mask)

        # Perform closing operation (Dilation followed by Erosion)
        raw_mask = cv2.inRange(masked_image, lower_bound, upper_bound)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
        closed_mask[center[1] - 1:center[1] + 2, center[0] + 1:] = \
            raw_mask[center[1] - 1:center[1] + 2, center[0] + 1:]

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed_mask, connectivity=8)

        # Function to check if a component forms a ring
        def is_full_ring(component_mask):
            # Find contours
            contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if len(contours) != 1:
                return False
            contour = contours[0]

            # Check if the contour is closed by checking if the start and end points are the same
            # if cv2.arcLength(contour, True) > 0:
            #     return True
            # Approximate the contour with polygons.
            epsilon = 0.05 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            # Check if the polygon is closed (has no gaps) and has a reasonable number of vertices for a ring.
            if not cv2.isContourConvex(approx) or len(approx) < 4:
                return False

            # All conditions met, likely a close ring.
            return True

        # output_image = image.copy()
        # Iterate over each component
        ring_count = 0
        is_full = False
        the_area = 0
        for label in range(1, num_labels):
            x, y, width, height, area = stats[label, :5]
            bounding_box_area = width * height
            component_mask = (labels == label).astype(np.uint8) * 255
            # color = colors[label % len(colors)]
            # mask = labels == label
            # output_image[mask] = color
            if bounding_box_area >= min_area:
                # Select a color from the list based on the label index
                if is_full_ring(component_mask):
                    is_full = True
                the_area = area
                ring_count += 1

        # Save or display the image with contours
        # cv2.imwrite(fr'test\count_rings_{is_full}_{self.screen_width}_mask.png', output_image)
        # cv2.imwrite(fr'test\count_rings_{is_full}_{self.screen_width}.png', masked_image)
        if ring_count > 1:
            is_full = False
            the_area = 0
            self.logger.warning(f'is_con_full found multiple rings {ring_count}')

        return the_area, is_full

    def update_lib_portrait_icon(self):
        # self.ensure_con_lib_boxes()
        for i in range(len(self.chars)):
            char_index = i + 1
            char = self.chars[i]
            if char and not char.is_current_char and char.ring_index >= 0 and not char._liberation_available:
                box = self.get_box_by_name("lib_mark_char_{}".format(char_index))
                match = self.find_one(lib_ready_templates[char.ring_index], box=box, threshold=0.8)
                if match:
                    char._liberation_available = True
                    self.log_debug('checking liberation_available by template {} {}'.format(char, match))


con_colors = [
    {
        'r': (205, 235),
        'g': (190, 222),  # for yellow spectro
        'b': (90, 130)
    },
    {
        'r': (150, 190),  # Red range
        'g': (95, 140),  # Green range for purple electric
        'b': (210, 249)  # Blue range
    },
    {
        'r': (200, 230),  # Red range
        'g': (100, 130),  # Green range    for red fire
        'b': (75, 105)  # Blue range
    },
    {
        'r': (60, 95),  # Red range
        'g': (150, 180),  # Green range    for blue ice
        'b': (210, 245)  # Blue range
    },
    {
        'r': (70, 110),  # Red range
        'g': (215, 250),  # Green range    for green wind
        'b': (155, 190)  # Blue range
    },
    {
        'r': (190, 220),  # Red range
        'g': (65, 105),  # Green range    for havoc
        'b': (145, 175)  # Blue range
    }
]

lib_ready_templates = [
    'lib_ready_spectro',  # 3
    'lib_ready_electric',  # 3
    'lib_ready_fire',  # 2
    'lib_ready_ice',  # 2
    'lib_ready_wind',  # 1
    'lib_ready_havoc',  # 3
]


def convert_cd(text):
    """
    Strips a string to only keep the first part that matches the regex pattern.
    Args:
      text: The input string.
      pattern: The regex pattern to match.
    Returns:
      The first matching substring, or None if no match is found.
    """
    try:
        return float(text.name)
    except ValueError:
        match = re.search(cd_regex, text.name)
        if match:
            return float(match.group(0))
        else:
            return 1
