# SPDX-License-Identifier: GPL-3.0-or-later
"""Original Qt Widgets interface for the independent Linux manager."""
from pathlib import Path
import os
import sys

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from . import NAME, VERSION
from .backend import CompilerNeeded
from . import steam

STYLE = """
QWidget { background: #11171d; color: #dfe7ec; font-size: 14px; }
QMainWindow { background: #11171d; }
QFrame#sidebar { background: #171f27; border-right: 1px solid #2a3540; }
QFrame#card { background: #19232d; border: 1px solid #2e3b47; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QLabel { background: transparent; }
QLabel#brand { font-size: 20px; font-weight: 700; color: #ffffff; }
QLabel#title { font-size: 29px; font-weight: 700; color: #ffffff; }
QLabel#gameTitle { font-size: 24px; font-weight: 700; color: #ffffff; }
QLabel#muted { color: #9daebb; }
QLabel#eyebrow { color: #5de1bc; font-size: 12px; font-weight: 700; }
QLabel#status { color: #5de1bc; font-weight: 600; }
QLabel#path { color: #aebdca; font-size: 12px; }
QLineEdit { background: #1b252f; border: 1px solid #33424f; border-radius: 7px; padding: 10px; }
QLineEdit:focus { border-color: #5de1bc; }
QListWidget { background: #141d25; border: 1px solid #2b3742; border-radius: 9px; padding: 5px; outline: none; }
QListWidget::item { padding: 16px 11px; border-radius: 6px; margin: 2px 0; }
QListWidget::item:selected { background: #263d40; color: #ffffff; }
QListWidget::item:hover { background: #21303c; }
QPushButton { background: #26323d; border: 1px solid #3b4a57; border-radius: 7px; padding: 8px 12px; min-height: 18px; font-weight: 600; }
QPushButton:hover { background: #324351; border-color: #607685; }
QPushButton:disabled { color: #6b7c89; background: #1b252e; border-color: #293640; }
QPushButton#primary { background: #5de1bc; border-color: #5de1bc; color: #10221d; }
QPushButton#primary:hover { background: #8beed2; }
QPushButton#primary:disabled { background: #244b43; color: #83a69a; border-color: #244b43; }
QPushButton#quiet { background: transparent; border-color: transparent; text-align: left; color: #aebdca; }
QPushButton#quiet:hover { background: #25323e; color: #ffffff; }
QPushButton#danger { color: #efb5a9; }
QTextEdit { background: #0e151b; border: 1px solid #2e3b47; border-radius: 7px; color: #adbdc9; padding: 8px; font-size: 12px; }
QProgressBar { background: #22313c; border: none; border-radius: 3px; height: 5px; }
QProgressBar::chunk { background: #5de1bc; }
QSplitter::handle { background: #11171d; width: 14px; }
QMessageBox QLabel { min-width: 370px; }
"""


class Worker(QThread):
    done = Signal(object)
    failed = Signal(object)
    progress = Signal(str)

    def __init__(self, controller, function):
        super().__init__()
        self.controller, self.function = controller, function

    def run(self):
        previous = self.controller.progress
        self.controller.progress = self.progress.emit
        try:
            self.done.emit(self.function())
        except Exception as error:
            self.failed.emit(error)
        finally:
            self.controller.progress = previous


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class Window(QMainWindow):
    library_ready = Signal()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.thread = self.worker = None
        self.busy = False
        self.game = None
        self.inspection = {}
        self.last_operation = None
        self.setWindowTitle(NAME)
        self.resize(1240, 900)
        self.setMinimumSize(1080, 850)
        outer = QWidget()
        self.setCentralWidget(outer)
        columns = QHBoxLayout(outer)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(228)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(21, 30, 18, 24)
        navigation.setSpacing(13)
        navigation.addWidget(label("NR  /  LINUX", "eyebrow"))
        navigation.addWidget(label("Neural rendering\nfor your library", "brand", True))
        navigation.addSpacing(25)
        self.library_button = self.button("▦  Game library", self.refresh, "quiet")
        navigation.addWidget(self.library_button)
        navigation.addWidget(self.button("＋  Add a game", self.add_game, "quiet"))
        navigation.addWidget(self.button("↻  Updates", self.check_updates, "quiet"))
        navigation.addStretch()
        navigation.addWidget(self.button("Settings and about", self.settings, "quiet"))
        navigation.addWidget(label(f"NR Linux Manager  {VERSION}\nIndependent community app", "muted", True))
        columns.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(29, 28, 29, 22)
        content.setSpacing(17)
        columns.addLayout(content, 1)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label("Your games", "title"))
        titles.addWidget(label("Install neural rendering. Keep your existing Steam setup.", "muted"))
        heading.addLayout(titles, 1)
        self.rescan = self.button("Rescan library", self.refresh)
        heading.addWidget(self.rescan)
        content.addLayout(heading)
        banner = QFrame()
        banner.setObjectName("card")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(17, 13, 17, 13)
        banner_layout.addWidget(label("NATIVE LINUX APP  ·  WINDOWS GAMES THROUGH PROTON", "eyebrow"))
        banner_layout.addWidget(label("Downloads, backups and Steam launch options are handled for you.\nClose the game and Steam before installing or changing a patch.", "muted", True))
        content.addWidget(banner)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search installed games…")
        self.search.textChanged.connect(self.filter_games)
        left_layout.addWidget(self.search)
        self.library = QListWidget()
        self.library.currentItemChanged.connect(self.select_game)
        left_layout.addWidget(self.library, 1)
        self.count = label("Finding your library…", "muted")
        left_layout.addWidget(self.count)
        split.addWidget(left)
        details = QFrame()
        details.setObjectName("card")
        detail = QVBoxLayout(details)
        detail.setContentsMargins(22, 23, 22, 20)
        detail.setSpacing(9)
        self.platform = label("YOUR LIBRARY", "eyebrow")
        self.title = label("Choose a game", "gameTitle", True)
        self.status = label("Select a game to see its installation.", "status", True)
        self.description = label("Installed Steam games appear automatically. You can also add a game from another library.", "muted", True)
        self.path = label("", "path", True)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.platform, self.title, self.status, self.description, self.path):
            detail.addWidget(widget)
        self.browse = self.button("Choose game executable…", self.choose_executable)
        detail.addWidget(self.browse)
        detail.addStretch(1)
        self.install = self.button("Install neural rendering", lambda: self.operate("install"), "primary")
        detail.addWidget(self.install)
        actions = QHBoxLayout()
        self.repair = self.button("Repair", lambda: self.operate("repair"))
        self.update = self.button("Update", lambda: self.operate("update"))
        actions.addWidget(self.repair)
        actions.addWidget(self.update)
        detail.addLayout(actions)
        self.launch = self.button("Launch in Steam ↗", self.launch_game)
        detail.addWidget(self.launch)
        utility = QHBoxLayout()
        self.folder = self.button("Game folder", self.open_folder, "quiet")
        self.logs = self.button("Save logs", self.save_logs, "quiet")
        utility.addWidget(self.folder)
        utility.addWidget(self.logs)
        detail.addLayout(utility)
        self.uninstall = self.button("Uninstall and restore originals", lambda: self.operate("uninstall"), "danger")
        detail.addWidget(self.uninstall)
        split.addWidget(details)
        split.setSizes([330, 470])
        content.addWidget(split, 1)
        self.activity = label("Ready", "muted", True)
        content.addWidget(self.activity)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        content.addWidget(self.progress)
        self.log_toggle = self.button("Show activity details", self.toggle_log, "quiet")
        content.addWidget(self.log_toggle)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        self.log.hide()
        content.addWidget(self.log)
        self.controls()

    def button(self, text, callback, name=None):
        button = QPushButton(text)
        if name:
            button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def controls(self):
        selected = self.game is not None
        resolved = selected and self.game.exe_dir is not None
        installed = self.inspection.get("installed", False)
        for widget in (self.library, self.rescan, self.library_button, self.browse, self.search):
            widget.setEnabled(not self.busy)
        self.browse.setVisible(selected)
        self.install.setEnabled(bool(resolved and not installed and not self.busy))
        for widget in (self.repair, self.update, self.uninstall):
            widget.setEnabled(bool(resolved and installed and not self.busy))
        unsafe = self.inspection.get("recovery_needed") or self.inspection.get("conflicts") or self.controller.pending_operation(self.game) if selected else False
        self.launch.setEnabled(bool(selected and self.game.appid and not self.busy and not unsafe))
        self.folder.setEnabled(bool(resolved and not self.busy))
        self.logs.setEnabled(bool(resolved and not self.busy))

    @Slot(str)
    def report(self, message):
        self.activity.setText(message)
        self.log.append(message.replace("&", "&amp;").replace("<", "&lt;"))

    def work(self, function, success):
        if self.busy:
            return
        self.busy = True
        self.controls()
        self.progress.show()
        self.success_callback = success
        self.worker = Worker(self.controller, function)
        self.thread = self.worker
        self.worker.progress.connect(self.report)
        self.worker.done.connect(self.work_succeeded)
        self.worker.failed.connect(self.failure)
        self.thread.finished.connect(self.work_finished)
        self.thread.start()

    @Slot(object)
    def work_succeeded(self, result):
        self.success_callback(result)

    @Slot()
    def work_finished(self):
        self.thread.wait()
        self.thread.deleteLater()
        self.busy = False
        self.thread = self.worker = None
        self.progress.hide()
        self.controls()
        # Selection refresh must wait until the worker has fully stopped.
        if getattr(self, "refresh_after_work", False):
            self.refresh_after_work = False
            QTimer.singleShot(0, lambda: self.select_game(self.library.currentItem()))

    @Slot(object)
    def failure(self, error):
        self.report(str(error))
        if isinstance(error, CompilerNeeded):
            QTimer.singleShot(100, self.compiler_needed)
        else:
            QMessageBox.warning(self, "Could not complete the operation", str(error))

    def compiler_needed(self):
        QMessageBox.information(self, "One file is needed", "No usable shader compiler was found automatically. Select a native d3dcompiler_47.dll from an installed Windows game. Wine's built-in copy is not suitable.")
        if self.pick_compiler() and self.last_operation:
            self.operate(self.last_operation)

    def refresh(self):
        self.work(self.controller.discover, self.show_games)

    @Slot(object)
    def show_games(self, games):
        selected = self.game
        self.library.blockSignals(True)
        self.library.clear()
        for game in games:
            item = QListWidgetItem(game.name)
            item.setData(Qt.ItemDataRole.UserRole, game)
            item.setToolTip(str(game.install_dir))
            self.library.addItem(item)
            if selected and game.appid == selected.appid and game.install_dir == selected.install_dir:
                self.library.setCurrentItem(item)
        if self.library.currentRow() < 0 and games:
            self.library.setCurrentRow(0)
        self.library.blockSignals(False)
        self.filter_games(self.search.text())
        self.count.setText(f"{len(games)} installed games")
        self.report("Library ready. Choose a game to get started.")
        self.refresh_after_work = True
        self.library_ready.emit()

    def filter_games(self, query):
        for i in range(self.library.count()):
            item = self.library.item(i)
            item.setHidden(query.casefold() not in item.text().casefold())

    def select_game(self, item, previous=None):
        if self.busy:
            return
        self.game = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.inspection = {}
        if not self.game:
            self.controls()
            return
        game = self.game
        self.title.setText(game.name)
        self.platform.setText(f"STEAM  /  {game.appid}" if game.appid else "MANUALLY ADDED GAME")
        self.path.setText(str(game.exe_dir or game.install_dir))
        self.status.setText("Checking installation…" if game.exe_dir else "Choose the game executable")
        self.description.setText("" if game.exe_dir else "Several executable locations may exist. Choose the main 64-bit DirectX 12 game executable to continue.")
        self.controls()
        if game.exe_dir:
            self.work(lambda: self.controller.backend.inspect(game.exe_dir), self.show_inspection)

    @Slot(object)
    def show_inspection(self, result):
        self.inspection = result
        if result.get("recovery_needed") or self.controller.pending_operation(self.game):
            self.status.setText("Interrupted operation")
            self.description.setText("Close Steam and this game, then use Install or Repair. The launcher will recover the previous operation first.")
        elif result.get("conflicts"):
            self.status.setText("Existing files need attention")
            self.description.setText("\n".join(result["conflicts"]))
        elif result.get("installed"):
            damaged = len(result.get("missing", [])) + len(result.get("modified", []))
            self.status.setText("Repair recommended" if damaged else "Neural rendering installed")
            self.description.setText(f"{damaged} managed files need attention. Repair restores them and keeps your configuration." if damaged else "Ready to launch with your existing Proton version and saves. Press Home in-game to toggle neural rendering.")
        elif result.get("conflicts"):
            self.status.setText("Existing files need attention")
            self.description.setText("The installer will check these files and explain any conflicts before making changes.")
        else:
            self.status.setText("Ready to install")
            self.description.setText("The launcher downloads the required runtime, finds a shader compiler, backs up affected files and sets Steam's launch options.")
        if result.get("running"):
            self.description.setText("This game is running. Save and close it before installing, repairing or removing neural rendering.")
        self.report("Installation check complete.")

    def add_game(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose the main Windows game executable", str(Path.home()), "Windows executables (*.exe *.EXE)")
        if path:
            try:
                self.controller.add_manual(path)
                self.refresh()
            except Exception as error:
                self.failure(error)

    def choose_executable(self):
        if not self.game or self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose the main 64-bit DirectX 12 executable", str(self.game.install_dir), "Windows executables (*.exe *.EXE)")
        if path:
            try:
                self.game = self.controller.set_executable(self.game, Path(path))
                self.library.currentItem().setData(Qt.ItemDataRole.UserRole, self.game)
                self.select_game(self.library.currentItem())
            except Exception as error:
                self.failure(error)

    def operate(self, action):
        if self.busy or not self.game:
            return
        if self.game.appid and steam.steam_is_running():
            QMessageBox.information(self, "Close Steam first", "Please exit Steam completely, then click the action again. Steam needs to be closed so it can keep the launch options we set for this game.")
            return
        if action == "uninstall":
            answer = QMessageBox.question(self, "Restore this game?", f"Remove neural rendering from {self.game.name} and restore its original files and Steam launch options?\n\nYour saves are kept.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.last_operation = action
        self.work(lambda: self.controller.operate(action, self.game), self.operation_done)

    @Slot(object)
    def operation_done(self, result):
        self.report(result["message"])
        self.refresh_after_work = True
        if result.get("launch_options"):
            QMessageBox.information(self, "Manual game launch option", result["message"] + "\n\n" + result["launch_options"])

    def check_updates(self):
        self.work(self.controller.check_updates, self.updates_done)

    @Slot(object)
    def updates_done(self, result):
        if result:
            message = f"Linux package {result['version']} is available. Select an installed game and click Update to apply it. To update this app, download the new native launcher from the release page."
        else:
            message = "This launcher includes the latest published Linux package. Update on a game reapplies the current verified package."
        self.report(message)
        QMessageBox.information(self, "Updates", message)

    def pick_compiler(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select a native Windows shader compiler", str(Path.home()), "Shader compiler (d3dcompiler_47.dll D3DCompiler_47.dll)")
        if not path:
            return False
        try:
            self.controller.choose_compiler(path)
            self.report("Shader compiler selected. It will be checked before installation.")
            return True
        except Exception as error:
            self.failure(error)
            return False

    def settings(self):
        if self.busy:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Settings & about")
        dialog.setText(f"{NAME} {VERSION}\n\nIndependent native Linux manager for the AMDNR Linux bridge. Tested GPU: RX 9070 XT. See the installation guide for game and Proton validation details. Other GPUs, games and Flatpak Steam need further testing.\n\nCompiler: {self.controller.settings.get('compiler') or 'Find automatically'}\n\nThe initial install downloads AMDNR and the pinned ROCm runtime directly from their publishers. Existing saves and Proton prefixes are retained.\n\nAMDNR, OptiScaler, Wine, vkd3d-proton, ROCm, Python and Qt belong to their respective authors. This is not the upstream AMDNR Launcher.\n\nAMDNR by 3zwr1 — https://github.com/3zwr1/AMD-NR---OptiScaler.")
        compiler = dialog.addButton("Choose compiler…", QMessageBox.ButtonRole.ActionRole)
        automatic = dialog.addButton("Use automatic detection", QMessageBox.ButtonRole.ActionRole)
        documentation = dialog.addButton("Installation guide ↗", QMessageBox.ButtonRole.ActionRole)
        notices = dialog.addButton("Licenses and source", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() == compiler:
            self.pick_compiler()
        elif dialog.clickedButton() == automatic:
            self.controller.settings["compiler"] = None
            self.controller.save_settings()
        elif dialog.clickedButton() == documentation:
            QDesktopServices.openUrl(QUrl("https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/blob/main/linux/INSTALL.md"))
        elif dialog.clickedButton() == notices:
            portable = os.environ.get("NRL_PORTABLE_ROOT")
            target = Path(portable) / "RUNTIME-LICENSES.md" if portable else Path(__file__).with_name("RUNTIME-LICENSES.md")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def launch_game(self):
        if self.game and self.game.appid:
            QDesktopServices.openUrl(QUrl(f"steam://rungameid/{self.game.appid}"))

    def open_folder(self):
        if self.game:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.game.exe_dir or self.game.install_dir)))

    def save_logs(self):
        if self.busy or not self.game:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save logs — review before sharing", str(Path.home() / "nr-linux-logs.zip"), "ZIP archive (*.zip)")
        if path:
            self.work(lambda: self.controller.collect_logs(self.game, Path(path)), lambda result: self.report(f"Logs saved to {result}. Review personal paths before sharing."))

    def toggle_log(self):
        self.log.setVisible(not self.log.isVisible())
        self.log_toggle.setText("Hide activity details" if self.log.isVisible() else "Show activity details")

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Operation in progress", "Please wait for the current operation to finish before closing the launcher.")
            event.ignore()
        else:
            event.accept()


def run(controller, snapshot=None):
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(NAME)
    application.setStyle("Fusion")
    application.setFont(QFont("DejaVu Sans", 10))
    application.setStyleSheet(STYLE)
    window = Window(controller)
    window.show()
    if snapshot:
        # This is an explicit, read-only rendering mode used by release tests.
        def capture_when_idle():
            if window.busy:
                QTimer.singleShot(100, capture_when_idle)
                return
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(snapshot))
            application.quit()
        window.library_ready.connect(lambda: QTimer.singleShot(300, capture_when_idle))
    QTimer.singleShot(0, window.refresh)
    application.exec()
