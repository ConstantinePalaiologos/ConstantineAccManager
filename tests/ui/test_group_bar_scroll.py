import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from utils.ui import _WheelToHorizontalScroll


class WheelToHorizontalScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_area(self, content_width):
        area = QScrollArea()
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.resize(content_width, 20)
        area.setWidget(inner)
        area.resize(100, 40)
        area.show()
        area.viewport().installEventFilter(_WheelToHorizontalScroll(area))
        self.app.processEvents()
        self.addCleanup(area.close)
        return area

    def spin(self, area, delta):
        event = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(area.viewport(), event)
        self.app.processEvents()

    def test_the_wheel_scrolls_wide_content_sideways_without_a_scroll_bar(self):
        area = self.make_area(400)
        bar = area.horizontalScrollBar()
        self.assertFalse(bar.isVisible())
        self.assertGreater(bar.maximum(), 0)
        self.spin(area, -120)
        self.assertGreater(bar.value(), 0)
        moved = bar.value()
        self.spin(area, 120)
        self.assertLess(bar.value(), moved)

    def test_scrolling_stops_at_the_ends(self):
        area = self.make_area(400)
        bar = area.horizontalScrollBar()
        for _ in range(10):
            self.spin(area, -120)
        self.assertEqual(bar.value(), bar.maximum())
        for _ in range(10):
            self.spin(area, 120)
        self.assertEqual(bar.value(), 0)

    def test_content_that_fits_is_left_alone(self):
        area = self.make_area(60)
        bar = area.horizontalScrollBar()
        self.assertEqual(bar.maximum(), 0)
        self.spin(area, -120)
        self.assertEqual(bar.value(), 0)


if __name__ == "__main__":
    unittest.main()
