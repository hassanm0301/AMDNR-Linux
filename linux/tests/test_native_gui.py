"""Exercise our own Qt interface offscreen; never launch or modify real games."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from native_launcher.app import Window, STYLE
    QT = True
except ImportError:
    QT = False
from native_launcher.steam import SteamGame


class FakeController:
    def __init__(self, root):
        self.root = root
        self.progress = lambda message: None
        self.games = [SteamGame("12", "A test game", root, root, root, root),
                      SteamGame("13", "Another adventure", root, root, root, None)]
        self.backend = self
        self.settings = {}
        self.installed = False
        self.operations = []
        self.conflicts = []
        self.pending = False

    def discover(self):
        return self.games

    def inspect(self, directory):
        return dict(installed=self.installed, modified=[], missing=[], conflicts=self.conflicts)

    def operate(self, action, game):
        self.operations.append((action, game.appid))
        self.installed = action != "uninstall"
        return {"message": "Completed fixture operation"}

    def pending_operation(self, game):
        return self.pending


@unittest.skipUnless(QT, "Optional GUI tests need PySide6")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native gui ")
        self.addCleanup(self.temp.cleanup)
        self.controller = FakeController(Path(self.temp.name))
        self.window = Window(self.controller)
        self.window.show()
        self.window.refresh()
        self.wait_idle()

    def tearDown(self):
        self.wait_idle()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_idle(self):
        deadline = time.monotonic() + 10
        quiet = 0
        while time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
            quiet = quiet + 1 if not self.window.busy else 0
            if quiet >= 3:
                return
        self.fail("GUI worker did not settle")

    def test_library_and_search(self):
        self.assertEqual(self.window.library.count(), 2)
        self.assertEqual(self.window.title.text(), "A test game")
        self.assertTrue(self.window.install.isEnabled())
        self.window.search.setText("adventure")
        self.assertTrue(self.window.library.item(0).isHidden())
        self.assertFalse(self.window.library.item(1).isHidden())

    def test_ambiguous_executable_requires_selection(self):
        self.window.library.setCurrentRow(1)
        self.wait_idle()
        self.assertFalse(self.window.install.isEnabled())
        self.assertIn("Choose", self.window.status.text())

    def test_install_repair_update_uninstall_buttons(self):
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            QTest.mouseClick(self.window.install, Qt.MouseButton.LeftButton)
            self.wait_idle()
            self.assertFalse(self.window.install.isEnabled())
            self.assertTrue(self.window.repair.isEnabled())
            for button in (self.window.repair, self.window.update):
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.wait_idle()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(self.window.uninstall, Qt.MouseButton.LeftButton)
                self.wait_idle()
        self.assertEqual([action for action, _ in self.controller.operations], ["install", "repair", "update", "uninstall"])
        self.assertTrue(self.window.install.isEnabled())

    def test_steam_running_prevents_operation(self):
        with patch("native_launcher.app.steam.steam_is_running", return_value=True), patch.object(QMessageBox, "information") as notice:
            QTest.mouseClick(self.window.install, Qt.MouseButton.LeftButton)
        self.assertEqual(self.controller.operations, [])
        notice.assert_called_once()

    def test_conflict_and_pending_state_prevent_launch(self):
        self.controller.conflicts = ["Original backup is missing"]
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.assertFalse(self.window.launch.isEnabled())
        self.assertIn("attention", self.window.status.text())
        self.controller.conflicts = []
        self.controller.pending = True
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.assertFalse(self.window.launch.isEnabled())
        self.assertEqual(self.window.status.text(), "Interrupted operation")

    def test_main_buttons_have_unclipped_content_height(self):
        for button in (self.window.install, self.window.repair, self.window.browse, self.window.uninstall):
            self.assertGreaterEqual(button.height(), button.fontMetrics().height() + 16)


if __name__ == "__main__":
    unittest.main()
