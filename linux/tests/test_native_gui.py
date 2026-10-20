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
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from native_launcher.app import BUTTON_HEIGHT, ROW_HEIGHT, SettingsDialog, Window, STYLE
    QT = True
except ImportError:
    QT = False
from native_launcher.steam import SteamGame


class FakeController:
    def __init__(self, root):
        self.root = root
        self.cache_dir = root / "artwork-cache"
        self.progress = lambda message: None
        self.games = [SteamGame("12", "A test game", root, root, root, root),
                      SteamGame("13", "Another adventure", root, root, root, None)]
        self.backend = self
        self.settings = {}
        self.installed = False
        self.operations = []
        self.conflicts = []
        self.health_issues = []
        self.missing = []
        self.modified = []
        self.pending = False
        self.latest_release = None
        self.proxy = "winmm.dll"
        self.proxy_history = []
        self.proxy_requests = []
        self.occupied_proxies = set()
        self.leftovers = None

    def discover(self):
        return self.games

    def inspect(self, directory):
        from proxies import SUPPORTED
        options = [{"name": name, "available": name not in self.occupied_proxies,
                    "current": self.installed and name == self.proxy} for name in SUPPORTED]
        result = dict(installed=self.installed, modified=self.modified, missing=self.missing, conflicts=self.conflicts,
                      health_issues=self.health_issues, runtime_status="unverified",
                      proxy_switch_supported=self.installed, proxy_switch_issue=None,
                      proxy_name=self.proxy if self.installed else None, proxy_history=list(self.proxy_history),
                      proxy_options=options, proxy_next=next((name for name in SUPPORTED
                          if name not in self.proxy_history and name not in self.occupied_proxies), None))
        if not self.installed:
            result["leftovers"] = dict(self.leftovers or dict(remove=[], restore=[], folders=[], prefix=[], kept=[],
                                                              issues=[], save_config=False, steam_wrapper=False))
        return result

    def operate(self, action, game, proxy=None):
        self.operations.append((action, game.appid))
        self.proxy_requests.append(proxy)
        if action == "clean":
            self.leftovers = None
            return {"message": "Leftover AMDNR files removed."}
        if action == "switch_proxy" and proxy == "next":
            proxy = self.inspect(game)["proxy_next"]
        if proxy and proxy != "auto":
            self.proxy = proxy
        if self.proxy not in self.proxy_history:
            self.proxy_history.append(self.proxy)
        self.installed = action != "uninstall"
        return {"message": "Completed fixture operation"}

    def pending_operation(self, game):
        return self.pending

    def check_updates(self):
        return self.latest_release

    def save_settings(self):
        pass

    def choose_compiler(self, path):
        self.settings["compiler"] = path


@unittest.skipUnless(QT, "Optional GUI tests need PySide6")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native gui ")
        self.addCleanup(self.temp.cleanup)
        # UI fixtures stay offline; artwork has separate cache/network tests.
        offline = patch("native_launcher.artwork_widgets.Store.load", return_value=None)
        offline.start()
        self.addCleanup(offline.stop)
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
        self.wait_idle()
        self.assertEqual(self.window.title.text(), "Another adventure")
        self.assertFalse(self.window.install.isEnabled())

    def test_empty_search_and_library_clear_selected_details(self):
        self.window.search.setText("no such game")
        self.wait_idle()
        self.assertIsNone(self.window.game)
        self.assertEqual(self.window.path.text(), "")
        self.assertEqual(self.window.status.text(), "")
        self.assertFalse(self.window.actions.isVisible())
        self.assertFalse(self.window.browse.isVisible())
        self.assertFalse(self.window.library.isVisible())
        self.assertIn("No matching", self.window.empty_library.text())
        self.window.search.clear()
        self.wait_idle()
        self.assertEqual(self.window.game.appid, "12")
        self.assertTrue(self.window.actions.isVisible())
        self.controller.games = []
        self.window.refresh()
        self.wait_idle()
        self.assertIsNone(self.window.game)
        self.assertEqual(self.window.path.text(), "")
        self.assertFalse(self.window.actions.isVisible())
        self.assertIn("No installed games", self.window.empty_library.text())

    def test_ambiguous_executable_requires_selection(self):
        self.window.library.setCurrentRow(1)
        self.wait_idle()
        self.assertFalse(self.window.install.isEnabled())
        self.assertIn("Choose", self.window.status.text())

    def test_ambiguous_steam_library_needs_explicit_manual_game(self):
        self.controller.games = [SteamGame("12", "Two copies", self.controller.root,
                                          self.controller.root, self.controller.root, None,
                                          library_ambiguous=True)]
        self.window.refresh()
        self.wait_idle()
        self.assertIn("Multiple Steam copies", self.window.status.text())
        self.assertIn("Add game", self.window.description.text())
        self.assertFalse(self.window.install.isEnabled())
        self.assertFalse(self.window.launch.isEnabled())
        self.assertFalse(self.window.browse.isEnabled())

    def test_install_repair_update_uninstall_buttons(self):
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            QTest.mouseClick(self.window.install, Qt.MouseButton.LeftButton)
            self.wait_idle()
            self.assertFalse(self.window.install.isEnabled())
            self.assertTrue(self.window.repair.isEnabled())
            self.assertIn("Files verified", self.window.status.text())
            self.assertIn("Enable DLSS, FSR or XeSS", self.window.description.text())
            self.assertIn("Insert opens OptiScaler", self.window.description.text())
            for button in (self.window.repair, self.window.update):
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.wait_idle()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(self.window.uninstall, Qt.MouseButton.LeftButton)
                self.wait_idle()
        self.assertEqual([action for action, _ in self.controller.operations], ["install", "repair", "update", "uninstall"])
        self.assertTrue(self.window.install.isEnabled())

    def test_leftovers_replace_uninstall_and_are_confirmed_first(self):
        self.assertFalse(self.window.clean.isVisible())
        self.assertTrue(self.window.uninstall.isVisible())
        self.controller.leftovers = dict(
            remove=["OptiScaler.log", "amdnr-linux/launch.log", "amdnr-linux/run.sh"], restore=["d3d12.dll"],
            folders=["OptiScaler"], prefix=[], kept=[{"path": "dxgi.dll", "reason": "an OptiScaler build that does not name AMDNR"}],
            issues=[], save_config=True, steam_wrapper=True,
            linked={"OptiScaler.log": "/mods/overwrite/OptiScaler.log", "amdnr-linux/run.sh": "/mods/overwrite/amdnr-linux/run.sh"})
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.assertTrue(self.window.clean.isVisible())
        self.assertTrue(self.window.clean.isEnabled())
        self.assertFalse(self.window.uninstall.isVisible())
        self.assertTrue(self.window.install.isEnabled())
        self.assertEqual(self.window.status.text(), "Leftover files found")
        self.assertIn("left 4 items behind. Original files it replaced are still in its backups.",
                      self.window.description.text())
        self.assertIn("2 are links to AMDNR files in /mods/overwrite, probably a mod manager's folder; those files "
                      "are removed too.", self.window.description.text())
        self.assertIn("Steam's launch options still start", self.window.description.text())
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
                QTest.mouseClick(self.window.clean, Qt.MouseButton.LeftButton)
                self.wait_idle()
            self.assertEqual(self.controller.operations, [])
            text = ask.call_args.args[2]
            self.assertIn("?\n\n• Delete the 2 AMDNR files the links below point to, in /mods/overwrite\n", text)
            for line in ("• Remove OptiScaler.log", "• Remove amdnr-linux/ (2 files)", "• Remove the empty OptiScaler/ folder",
                         "• Put back the original d3d12.dll", "• Take the AMDNR wrapper out of Steam's launch options",
                         "• dxgi.dll: an OptiScaler build that does not name AMDNR", "OptiScaler.ini is first copied"):
                self.assertIn(line, text)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                QTest.mouseClick(self.window.clean, Qt.MouseButton.LeftButton)
                self.wait_idle()
        self.assertEqual(self.controller.operations, [("clean", "12")])
        self.assertFalse(self.window.clean.isVisible())
        self.assertTrue(self.window.uninstall.isVisible())
        self.assertEqual(self.window.status.text(), "Ready to install")
        self.controller.leftovers = dict(remove=["OptiScaler.log"], restore=[], folders=[], prefix=[], kept=[],
                                         issues=["Symlink in installation path: OptiScaler"], save_config=False,
                                         steam_wrapper=False)
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.assertTrue(self.window.clean.isVisible())
        self.assertFalse(self.window.clean.isEnabled())
        self.assertIn("cannot be removed yet:\nSymlink in installation path", self.window.description.text())

    def test_steam_running_prevents_operation(self):
        with patch("native_launcher.app.steam.steam_is_running", return_value=True), patch.object(QMessageBox, "information") as notice:
            QTest.mouseClick(self.window.install, Qt.MouseButton.LeftButton)
        self.assertEqual(self.controller.operations, [])
        notice.assert_called_once()

    def test_advanced_manual_install_and_next_proxy(self):
        QTest.mouseClick(self.window.proxy_toggle, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.proxy_panel.isVisible())
        self.window.proxy_choice.setCurrentIndex(self.window.proxy_choice.findData("version.dll"))
        self.assertFalse(self.window.proxy_apply.isEnabled())
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            QTest.mouseClick(self.window.install, Qt.MouseButton.LeftButton)
            self.wait_idle()
            self.assertEqual(self.controller.proxy_requests[-1], "version.dll")
            self.assertIn("version.dll", self.window.proxy_summary.text())
            self.window.proxy_choice.setCurrentIndex(self.window.proxy_choice.findData("dbghelp.dll"))
            self.assertTrue(self.window.proxy_apply.isEnabled())
            QTest.mouseClick(self.window.proxy_apply, Qt.MouseButton.LeftButton)
            self.wait_idle()
            self.assertEqual(self.controller.proxy, "dbghelp.dll")
            QTest.mouseClick(self.window.proxy_next, Qt.MouseButton.LeftButton)
            self.wait_idle()
            self.assertEqual(self.controller.proxy_requests[-1], "next")
            self.assertEqual(self.controller.proxy, "winmm.dll")

    def test_occupied_proxy_is_disabled_and_unsafe_install_cannot_switch(self):
        self.controller.installed = True
        self.controller.proxy_history = ["winmm.dll"]
        self.controller.occupied_proxies.add("version.dll")
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        index = self.window.proxy_choice.findData("version.dll")
        self.assertFalse(self.window.proxy_choice.model().item(index).isEnabled())
        self.controller.modified = ["winmm.dll"]
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.window.proxy_choice.setCurrentIndex(self.window.proxy_choice.findData("dbghelp.dll"))
        self.assertFalse(self.window.proxy_apply.isEnabled())
        self.assertFalse(self.window.proxy_next.isEnabled())

    def test_failed_game_inspection_does_not_reuse_previous_proxy_choice(self):
        self.window.proxy_choice.setCurrentIndex(self.window.proxy_choice.findData("version.dll"))
        with patch.object(self.controller, "inspect", side_effect=OSError("unreadable game")), \
                patch.object(QMessageBox, "warning"):
            self.window.select_game(self.window.library.currentItem())
            self.wait_idle()
        self.assertEqual(self.window.proxy_choice.currentData(), "auto")
        self.assertFalse(self.window.proxy_apply.isEnabled())

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

    def test_runtime_health_issues_require_repair_without_claiming_inference(self):
        self.controller.installed = True
        self.controller.health_issues = ["The launch wrapper is not executable.", "The Python runtime is missing."]
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.assertEqual(self.window.status.text(), "Repair recommended")
        self.assertIn("Python runtime", self.window.description.text())
        self.assertFalse(self.window.launch.isEnabled())
        self.assertTrue(self.window.repair.isEnabled())
        self.controller.health_issues = []
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()
        self.assertTrue(self.window.launch.isEnabled())
        self.assertIn("Files verified", self.window.status.text())

    def test_damaged_managed_binaries_prevent_launch_until_repaired(self):
        self.controller.installed = True
        for field in ("missing", "modified"):
            with self.subTest(field=field):
                setattr(self.controller, field, ["amdhip64.dll"])
                self.window.select_game(self.window.library.currentItem())
                self.wait_idle()
                self.assertEqual(self.window.status.text(), "Repair recommended")
                self.assertFalse(self.window.launch.isEnabled())
                self.assertTrue(self.window.repair.isEnabled())
                setattr(self.controller, field, [])

    def test_failed_operation_refreshes_installation_health(self):
        def failed_install(action, game, **kwargs):
            self.controller.installed = True
            self.controller.conflicts = ["An original backup needs attention after recovery."]
            raise RuntimeError("Fixture operation failed")

        with patch.object(self.controller, "operate", side_effect=failed_install), \
                patch("native_launcher.app.steam.steam_is_running", return_value=False), \
                patch.object(QMessageBox, "warning") as notice:
            self.window.operate("install")
            self.wait_idle()
        notice.assert_called_once()
        self.assertIn("original backup", self.window.description.text())
        self.assertFalse(self.window.launch.isEnabled())

    def test_resizing_preserves_button_sizes_and_scrolls_all_actions(self):
        self.window.title.setText("A long game title with a complete edition and an expansion")
        self.window.path.setText("/mnt/game-library/" + "long-folder-name/" * 14 + "Binaries/Win64")
        for width, height in ((800, 600), (1100, 720), (1920, 1080), (800, 600)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                for button in self.window.findChildren(QPushButton):
                    self.assertEqual(button.height(), BUTTON_HEIGHT)
                self.assertEqual(self.window.library.visualItemRect(self.window.library.item(0)).height(), ROW_HEIGHT)
                self.assertGreaterEqual(self.window.split.handleWidth(), 8)
                viewport = self.window.detail_scroll.viewport()
                self.assertLessEqual(self.window.detail_content.width(), viewport.width())
                for text in (self.window.title, self.window.description, self.window.path):
                    self.assertGreaterEqual(text.height(), text.heightForWidth(text.width()))
                if width == 800:
                    self.assertGreater(self.window.detail_scroll.verticalScrollBar().maximum(), 0)
                self.window.detail_scroll.ensureWidgetVisible(self.window.uninstall, 0, 0)
                self.app.processEvents()
                position = self.window.uninstall.mapTo(viewport, QPoint(0, 0))
                self.assertGreaterEqual(position.y(), 0)
                self.assertLessEqual(position.y() + self.window.uninstall.height(), viewport.height())
        self.window.toggle_log()
        self.app.processEvents()
        self.assertEqual(self.window.height(), 600)
        self.assertEqual(self.window.uninstall.height(), BUTTON_HEIGHT)

    def settings_dialog(self):
        dialog = SettingsDialog(self.window)
        dialog.show()
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def wait_settings(self, dialog):
        deadline = time.monotonic() + 10
        while dialog.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertIsNone(dialog.worker, "Settings worker did not settle")
        self.app.processEvents()

    def test_settings_update_check_and_compiler_controls(self):
        self.controller.latest_release = {"version": "9.0.0"}
        dialog = self.settings_dialog()
        dialog.resize(540, 420)
        dialog.check_updates()
        self.assertFalse(dialog.close_button.isEnabled())
        self.assertFalse(dialog.compiler_button.isEnabled())
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        self.wait_settings(dialog)
        self.assertIn("9.0.0", dialog.update_status.text())
        self.assertTrue(dialog.release_button.isVisible())
        self.assertTrue(dialog.close_button.isEnabled())
        with patch("native_launcher.app.QFileDialog.getOpenFileName", return_value=("/fixture/d3dcompiler_47.dll", "")):
            dialog.choose_compiler()
        self.assertEqual(self.controller.settings["compiler"], "/fixture/d3dcompiler_47.dll")
        self.assertIn("/fixture/", dialog.compiler_label.text())
        dialog.automatic_compiler()
        self.assertIsNone(self.controller.settings["compiler"])
        for button in dialog.findChildren(QPushButton):
            self.assertEqual(button.height(), BUTTON_HEIGHT)
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(dialog.scroll.widget().width(), dialog.scroll.viewport().width())

    def test_settings_failed_update_is_readable_and_can_retry(self):
        dialog = self.settings_dialog()
        with patch.object(self.controller, "check_updates", side_effect=RuntimeError("Network unavailable")):
            dialog.check_updates()
            self.wait_settings(dialog)
        self.assertIn("Network unavailable", dialog.update_status.text())
        self.assertTrue(dialog.check_button.isEnabled())
        self.assertFalse(dialog.release_button.isVisible())
        dialog.check_updates()
        self.wait_settings(dialog)
        self.assertIn("latest published", dialog.update_status.text())


if __name__ == "__main__":
    unittest.main()
