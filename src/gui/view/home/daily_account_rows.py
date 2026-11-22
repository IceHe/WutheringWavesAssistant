from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import ComboBox, FluentIcon, LineEdit, ToolButton

from src.config.config import normalize_daily_account_suffix, split_daily_account_suffixes


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
        self.suffix = LineEdit(self)
        self.suffix.setFixedWidth(116)
        self.dungeon = ComboBox(self)
        self.remove = ToolButton(FluentIcon.CLOSE, self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.handle)
        layout.addWidget(self.suffix)
        layout.addWidget(self.dungeon, 1)
        layout.addWidget(self.remove)


class DailyAccountRows(QWidget):
    changed = Signal()

    def __init__(self, dungeons, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._dungeons = dungeons
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

    def _add_row(self, suffix="", dungeon=None):
        row = _AccountRow(self)
        row.handle.setAccessibleName(self.tr("Drag to reorder account"))
        row.handle.setToolTip(self.tr("Drag to reorder account"))
        row.suffix.setAccessibleName(self.tr("Account suffix"))
        row.suffix.setPlaceholderText(self.tr("Account suffix"))
        row.dungeon.setAccessibleName(self.tr("Account Tacet Field"))
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
        row.dungeon.currentIndexChanged.connect(self.changed)
        row.remove.clicked.connect(lambda: self._remove_row(row))
        self.rows_layout.addWidget(row)
        return row

    def _add_blank_row(self):
        self._add_row().suffix.setFocus()

    def _remove_row(self, row):
        self.rows_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.changed.emit()

    def restore(self, suffix_text, dungeons):
        dungeons = {suffix.upper(): dungeon for suffix, dungeon in dungeons.items()}
        for row in self._rows():
            self.rows_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        for suffix in split_daily_account_suffixes(suffix_text):
            self._add_row(suffix, dungeons.get(suffix.upper()))
        if not self.rows_layout.count():
            self._add_row()

    def configuration(self, *, require_minimum=False):
        suffixes = []
        overrides = {}
        for index, row in enumerate(self._rows(), 1):
            text = row.suffix.text().strip()
            dungeon = row.dungeon.currentData()
            if not text and dungeon is None:
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
                if not any(item.id == dungeon for item in self._dungeons):
                    raise ValueError(self.tr("Choose a valid Tacet Field on row {row}.").format(row=index))
                overrides[suffix] = dungeon
            suffixes.append(suffix)
        if require_minimum and len(suffixes) < 2:
            raise ValueError(self.tr("Enter at least two different account suffixes."))
        return ", ".join(suffixes), overrides

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
