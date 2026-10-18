# SPDX-License-Identifier: GPL-3.0-or-later
"""Background artwork jobs; QPixmap and widget updates stay on the UI thread."""
from collections import OrderedDict

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .artwork import Store


def decode_image(data):
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    if data[:4] == b"\0\0\1\0":
        # Executables commonly list a 16px icon first. Use their largest frame
        # so the selected-game banner is not an enlarged taskbar icon.
        best, best_area = 0, 0
        for index in range(min(reader.imageCount(), 64)):
            if reader.jumpToImage(index):
                frame = reader.size()
                area = frame.width() * frame.height()
                if frame.isValid() and frame.width() <= 8192 and frame.height() <= 8192 and best_area < area <= 16_000_000:
                    best, best_area = index, area
        reader.jumpToImage(best)
    size = reader.size()
    if not size.isValid() or size.width() > 8192 or size.height() > 8192 or size.width() * size.height() > 16_000_000:
        return None
    if size.width() > 1200 or size.height() > 600:
        reader.setScaledSize(size.scaled(1200, 600, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return None if image.isNull() else image


class _Signals(QObject):
    loaded = Signal(object, object)


class _Task(QRunnable):
    def __init__(self, store, source, kind):
        super().__init__()
        self.store, self.source, self.kind = store, source, kind
        self.signals = _Signals()

    def run(self):
        try:
            image = self.store.load(self.source, self.kind, decode_image)
        except Exception:
            image = None
        self.signals.loaded.emit((self.source.key, self.kind), image)


class ArtworkLoader(QObject):
    ready = Signal(str, str, object)

    def __init__(self, cache_dir, parent=None):
        super().__init__(parent)
        self.store = Store(cache_dir)
        # Application ownership keeps active jobs alive if a window is closed.
        self.pool = QThreadPool(QApplication.instance())
        self.pool.setMaxThreadCount(3)
        self.pending = {}
        self.images = OrderedDict()
        self.closed = False

    def request(self, source, kind):
        if self.closed:
            return
        key = source.key, kind
        if key in self.images:
            self.images.move_to_end(key)
            self.ready.emit(*key, self.images[key])
        elif key not in self.pending:
            task = _Task(self.store, source, kind)
            task.signals.loaded.connect(self._loaded)
            self.pending[key] = task
            self.pool.start(task, 1 if kind == "banner" else 0)

    @Slot(object, object)
    def _loaded(self, key, image):
        self.pending.pop(key, None)
        if self.closed:
            return
        self.images[key] = image
        while len(self.images) > 128:
            self.images.popitem(last=False)
        self.ready.emit(*key, image)

    def close(self):
        self.closed = True
        self.pool.clear()


def placeholder():
    pixmap = QPixmap(84, 42)
    pixmap.fill(QColor("#263d40"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#9daebb"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "NR")
    painter.end()
    return pixmap


class Banner(QWidget):
    """Crop Steam banners; center executable icons without stretching them."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.is_icon = False
        self.setFixedHeight(158)
        self.setMinimumWidth(0)
        self.setAccessibleName("Selected game artwork")

    def set_image(self, image, is_icon=False):
        self.image, self.is_icon = image, is_icon
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#101b23"))
        if self.image is None:
            painter.setPen(QColor("#607685"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Game artwork unavailable")
            return
        pixmap = QPixmap.fromImage(self.image)
        if self.is_icon:
            pixmap = pixmap.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        else:
            pixmap = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((self.width() - pixmap.width()) // 2,
                           (self.height() - pixmap.height()) // 2, pixmap)
