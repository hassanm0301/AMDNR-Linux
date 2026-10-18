"""Runtime/plugin selection and game notices in our Qt interface, offscreen."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_gui_fixtures", HERE / "test_native_gui.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

if fixtures.QT:
    from PySide6.QtWidgets import QMessageBox


class ComponentController(fixtures.FakeController):
    def __init__(self, root):
        super().__init__(root)
        self.addon_version = "0.2.2"
        self.components_installed = None
        self.selection = {"daniel": None, "optipatcher": None}
        self.saved = []
        self.game_notices = []
        self.offer_hosts = False

    def inspect(self, directory):
        result = super().inspect(directory)
        result.update(addon_version="0.2.2" if self.installed else None, components=self.components_installed,
                      notices=list(self.game_notices),
                      component_choices=dict(supported=True, gpu={"gfx": "gfx1201", "generation": "rdna4", "apu": False},
                                             host_version="0.3.5.5R4", runtimes=["0.6.0", "0.5.1"],
                                             optipatcher=["0.41", "0.40"], selected=dict(self.selection)))
        if self.offer_hosts:
            result["component_choices"].update(
                hosts=[dict(key="amdnr", title="AMDNR 0.3.5.5R4", builtin_runtime=True),
                       dict(key="presr", title="OptiScaler AMD pre-SR 1.9.1-alpha", author="TheAutomatic",
                            builtin_runtime=False)],
                host_runtimes={"amdnr": ["0.6.0", "0.5.1"], "presr": ["0.3.1"]})
        return result

    def set_components(self, game, daniel=None, optipatcher=None, **host):
        self.selection = {"daniel": daniel, "optipatcher": optipatcher, **host}
        self.saved.append(dict(self.selection))
        return self.selection


@unittest.skipUnless(fixtures.QT, "Optional GUI tests need PySide6")
class ComponentGuiTests(unittest.TestCase):
    tearDown = fixtures.GuiTests.tearDown
    wait_idle = fixtures.GuiTests.wait_idle

    @classmethod
    def setUpClass(cls):
        fixtures.GuiTests.setUpClass.__func__(cls)

    def setUp(self):
        original = fixtures.FakeController
        fixtures.FakeController = ComponentController
        try:
            fixtures.GuiTests.setUp(self)
        finally:
            fixtures.FakeController = original

    def select_first(self):
        # Inspect again: the fixture state is set after the window's first scan.
        self.window.library.setCurrentRow(0)
        self.window.select_game(self.window.library.currentItem())
        self.wait_idle()

    def test_install_saves_the_chosen_runtime_and_plugin(self):
        self.select_first()
        window = self.window
        self.assertTrue(window.component_panel.isVisible())
        self.assertEqual([window.runtime_choice.itemData(i) for i in range(window.runtime_choice.count())],
                         [None, "0.6.0", "0.5.1"])
        self.assertFalse(window.components_apply.isVisible())
        self.assertFalse(window.host_choice.isVisible())
        window.runtime_choice.setCurrentIndex(1)
        window.optipatcher_check.setChecked(True)
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            window.install.click()
            self.wait_idle()
        self.assertEqual(self.controller.saved, [{"daniel": "0.6.0", "optipatcher": "0.41"}])
        self.assertEqual(self.controller.operations, [("install", "12")])

    def test_apply_is_offered_only_for_a_changed_selection(self):
        self.controller.installed = True
        self.controller.components_installed = {"neural_runtime": "daniel", "daniel": {"version": "0.6.0"}, "optipatcher": None}
        self.controller.selection = {"daniel": "0.6.0", "optipatcher": None}
        self.select_first()
        window = self.window
        self.assertEqual(window.runtime_choice.currentData(), "0.6.0")
        self.assertTrue(window.components_apply.isVisible())
        self.assertFalse(window.components_apply.isEnabled())
        window.runtime_choice.setCurrentIndex(0)
        self.assertTrue(window.components_apply.isEnabled())
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            window.components_apply.click()
            self.wait_idle()
        self.assertEqual(self.controller.saved[-1], {"daniel": None, "optipatcher": None})
        self.assertEqual(self.controller.operations, [("repair", "12")])

    def test_alternate_host_lists_only_its_runtimes_and_is_saved(self):
        self.controller.offer_hosts = True
        self.select_first()
        window = self.window
        self.assertTrue(window.host_choice.isVisible())
        self.assertEqual([window.host_choice.itemData(i) for i in range(window.host_choice.count())], ["amdnr", "presr"])
        self.assertNotIn("independent OptiScaler fork", window.component_note.text())
        window.host_choice.setCurrentIndex(1)
        self.assertEqual([window.runtime_choice.itemData(i) for i in range(window.runtime_choice.count())], ["0.3.1"])
        self.assertIn("independent OptiScaler fork by TheAutomatic", window.component_note.text())
        window.host_choice.setCurrentIndex(0)
        self.assertEqual([window.runtime_choice.itemData(i) for i in range(window.runtime_choice.count())],
                         [None, "0.6.0", "0.5.1"])
        window.host_choice.setCurrentIndex(1)
        with patch("native_launcher.app.steam.steam_is_running", return_value=False):
            window.install.click()
            self.wait_idle()
        self.assertEqual(self.controller.saved, [{"daniel": "0.3.1", "optipatcher": None, "host": "presr"}])
        self.assertEqual(self.controller.operations, [("install", "12")])

    def test_blocking_notice_asks_before_installing(self):
        self.controller.game_notices = [{"level": "confirm", "text": "This game ships BattlEye."},
                                        {"level": "info", "text": "A mod loader is present."}]
        self.select_first()
        self.assertTrue(self.window.notices.isVisible())
        self.assertIn("BattlEye", self.window.notices.text())
        with patch("native_launcher.app.steam.steam_is_running", return_value=False), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            self.window.install.click()
            self.wait_idle()
        question.assert_called_once()
        self.assertEqual(self.controller.operations, [])
        self.assertEqual(self.controller.saved, [])


if __name__ == "__main__":
    unittest.main()
