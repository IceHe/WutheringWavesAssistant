from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CheckBox, ComboBox, FluentIcon, LineEdit, MessageBoxBase, PushButton, SubtitleLabel, ToolButton

from src.config.config import TacetNestSelection, normalize_daily_account_suffix, split_daily_account_suffixes


_ROW_MIME = "application/x-wwa-daily-account-row"


class _DragHandle(QLabel):
    def __init__(self, parent):
        super().__init__("⠿", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(24)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._press = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_ROW_MIME, b"")
        drag.setMimeData(mime)
        drag.setPixmap(self.parentWidget().grab())
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)


class _AccountRow(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.handle = _DragHandle(self)
        self.enabled_checkbox = CheckBox(parent=self)
        self.suffix = LineEdit(self)
        self.suffix.setFixedWidth(116)
        self.dungeon = ComboBox(self)
        self.nests = PushButton(parent=self)
        self.nest_selection: TacetNestSelection | None = None
        self.remove = ToolButton(FluentIcon.CLOSE, self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.handle)
        layout.addWidget(self.enabled_checkbox)
        layout.addWidget(self.suffix)
        layout.addWidget(self.dungeon, 1)
        layout.addWidget(self.nests)
        layout.addWidget(self.remove)


class _NestSelectionDialog(MessageBoxBase):
    def __init__(self, options, selection: TacetNestSelection | None, parent):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(self.tr("Tacet Discord Nests"), self.widget))
        self.inherit = CheckBox(self.tr("Use global setting"), self.widget)
        self.viewLayout.addWidget(self.inherit)
        actions = QHBoxLayout()
        self.select_all = PushButton(self.tr("Select all"), self.widget)
        self.skip = PushButton(self.tr("Skip"), self.widget)
        actions.addWidget(self.select_all)
        actions.addWidget(self.skip)
        self.viewLayout.addLayout(actions)
        self.checks = {}
        for nest_id, label in options.items():
            checkbox = CheckBox(label, self.widget)
            checkbox.setChecked(selection == "All" or nest_id in (selection or []))
            self.viewLayout.addWidget(checkbox)
            self.checks[nest_id] = checkbox
        self.inherit.toggled.connect(self._set_inherit)
        self.inherit.setChecked(selection is None)
        self.select_all.clicked.connect(lambda: self._set_all(True))
        self.skip.clicked.connect(lambda: self._set_all(False))
        self.yesButton.setText(self.tr("Save"))
        self.cancelButton.setText(self.tr("Cancel"))

    def _set_inherit(self, inherit):
        for checkbox in self.checks.values():
            checkbox.setEnabled(not inherit)

    def _set_all(self, checked):
        self.inherit.setChecked(False)
        for checkbox in self.checks.values():
            checkbox.setChecked(checked)

    def selection(self) -> TacetNestSelection | None:
        if self.inherit.isChecked():
            return None
        selected = [nest_id for nest_id, checkbox in self.checks.items() if checkbox.isChecked()]
        return "All" if selected and len(selected) == len(self.checks) else selected


class DailyAccountRows(QWidget):
    changed = Signal()

    def __init__(self, dungeons, nests, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._dungeons = dungeons
        self._nests = {item.id: f"{item.name} - {item.region}" for item in nests if item.enable}
        self._drop_before = None
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(1, 3, 1, 3)
        self.rows_layout.setSpacing(6)
        self.add_button = ToolButton(FluentIcon.ADD, self)
        self.add_button.setAccessibleName(self.tr("Add account"))
        self.add_button.setToolTip(self.tr("Add account"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.rows_layout)
        layout.addWidget(self.add_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.add_button.clicked.connect(self._add_blank_row)

    def _rows(self):
        return [self.rows_layout.itemAt(index).widget() for index in range(self.rows_layout.count())]

    def _add_row(self, suffix="", dungeon=None, nests=None, *, enabled=True):
        row = _AccountRow(self)
        row.handle.setAccessibleName(self.tr("Drag to reorder account"))
        row.handle.setToolTip(self.tr("Drag to reorder account"))
        enabled_label = self.tr("Run dailies for this account")
        row.enabled_checkbox.setAccessibleName(enabled_label)
        row.enabled_checkbox.setToolTip(enabled_label)
        row.enabled_checkbox.setChecked(enabled)
        row.suffix.setAccessibleName(self.tr("Account suffix"))
        row.suffix.setPlaceholderText(self.tr("Account suffix"))
        row.dungeon.setAccessibleName(self.tr("Account Tacet Field"))
        row.nests.clicked.connect(lambda: self._edit_nests(row))
        self._set_nests(row, nests)
        row.remove.setAccessibleName(self.tr("Remove account"))
        row.remove.setToolTip(self.tr("Remove account"))
        row.suffix.setText(suffix)
        row.dungeon.addItem(self.tr("Use global setting"), userData=None)
        for item in self._dungeons:
            row.dungeon.addItem(f"{item.name} - {item.region}", userData=item.id)
            row.dungeon.setItemEnabled(row.dungeon.count() - 1, item.enable)
        if dungeon is not None:
            index = row.dungeon.findData(dungeon)
            if index < 0:
                row.dungeon.addItem(dungeon, userData=dungeon)
                index = row.dungeon.count() - 1
            row.dungeon.setCurrentIndex(index)
        row.suffix.textChanged.connect(self.changed)
        row.enabled_checkbox.toggled.connect(self.changed)
        row.dungeon.currentIndexChanged.connect(self.changed)
        row.remove.clicked.connect(lambda: self._remove_row(row))
        self.rows_layout.addWidget(row)
        return row

    def _set_nests(self, row, selection):
        row.nest_selection = selection
        if selection is None:
            summary = self.tr("Nests: use global setting")
        elif selection == "All":
            summary = self.tr("Nests: all")
        elif not selection:
            summary = self.tr("Nests: skip")
        else:
            summary = self.tr("Nests: {count} selected").format(count=len(selection))
        row.nests.setText(summary)
        row.nests.setToolTip("\n".join(self._nests.get(nest_id, nest_id) for nest_id in selection)
                            if isinstance(selection, list) and selection else summary)

    def _edit_nests(self, row):
        dialog = _NestSelectionDialog(self._nests, row.nest_selection, self.window())
        if dialog.exec():
            selection = dialog.selection()
            if selection != row.nest_selection:
                self._set_nests(row, selection)
                self.changed.emit()
        dialog.deleteLater()

    def _add_blank_row(self):
        self._add_row().suffix.setFocus()

    def _remove_row(self, row):
        self.rows_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.changed.emit()

    def restore(self, suffix_text, dungeons, nests, disabled_suffixes):
        dungeons = {suffix.upper(): dungeon for suffix, dungeon in dungeons.items()}
        nests = {suffix.upper(): selection for suffix, selection in nests.items()}
        disabled_suffixes = {normalize_daily_account_suffix(suffix) for suffix in disabled_suffixes}
        for row in self._rows():
            self.rows_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        for suffix in split_daily_account_suffixes(suffix_text):
            key = suffix.upper()
            self._add_row(suffix, dungeons.get(key), nests.get(key), enabled=key not in disabled_suffixes)
        if not self.rows_layout.count():
            self._add_row()

    def configuration(self, *, require_minimum=False):
        suffixes = []
        overrides = {}
        nest_overrides = {}
        disabled_suffixes = []
        for index, row in enumerate(self._rows(), 1):
            text = row.suffix.text().strip()
            dungeon = row.dungeon.currentData()
            enabled = row.enabled_checkbox.isChecked()
            if not text and dungeon is None and row.nest_selection is None:
                continue
            if not text:
                raise ValueError(self.tr("Enter an account suffix on row {row}.").format(row=index))
            try:
                suffix = normalize_daily_account_suffix(text)
            except ValueError:
                raise ValueError(self.tr("Use four letters or digits on row {row}.").format(row=index)) from None
            if suffix in suffixes:
                raise ValueError(self.tr("Account suffix {suffix} is already listed.").format(suffix=suffix))
            if dungeon is not None:
                if enabled and not any(item.id == dungeon for item in self._dungeons):
                    raise ValueError(self.tr("Choose a valid Tacet Field on row {row}.").format(row=index))
                overrides[suffix] = dungeon
            if row.nest_selection is not None:
                nest_overrides[suffix] = row.nest_selection
            suffixes.append(suffix)
            if not enabled:
                disabled_suffixes.append(suffix)
        if require_minimum and len(suffixes) == len(disabled_suffixes):
            raise ValueError(self.tr("Select at least one account for dailies."))
        return ", ".join(suffixes), overrides, nest_overrides, disabled_suffixes

    def _dragged_row(self, event):
        handle = event.source()
        if not event.mimeData().hasFormat(_ROW_MIME) or not isinstance(handle, _DragHandle):
            return None
        row = handle.parentWidget()
        return row if self.rows_layout.indexOf(row) >= 0 else None

    def _insertion_index(self, position):
        rows = self._rows()
        if (not rows or not self.rect().contains(position)
                or position.y() < rows[0].geometry().top() - 3 or position.y() > rows[-1].geometry().bottom() + 3):
            return None
        for index, row in enumerate(rows):
            if position.y() < row.geometry().center().y():
                return index
        return len(rows)

    def _show_insertion(self, index):
        self._drop_before = index
        self.update()

    def dragEnterEvent(self, event):
        if self._dragged_row(event) is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        index = self._insertion_index(event.position().toPoint())
        self._show_insertion(index)
        if index is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._show_insertion(None)

    def dropEvent(self, event):
        row = self._dragged_row(event)
        index = self._insertion_index(event.position().toPoint())
        self._show_insertion(None)
        if row is None or index is None:
            event.ignore()
            return
        self._move_row(row, index)
        event.acceptProposedAction()

    def _move_row(self, row, before):
        previous = self.rows_layout.indexOf(row)
        if before > previous:
            before -= 1
        if before != previous:
            self.rows_layout.removeWidget(row)
            self.rows_layout.insertWidget(before, row)
            self.changed.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        index = self._drop_before
        if index is None:
            return
        rows = self._rows()
        if index == 0:
            y = rows[0].geometry().top() - 2
        elif index == len(rows):
            y = rows[-1].geometry().bottom() + 2
        else:
            y = (rows[index - 1].geometry().bottom() + rows[index].geometry().top()) // 2
        painter = QPainter(self)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 2))
        painter.drawLine(2, y, self.width() - 2, y)
