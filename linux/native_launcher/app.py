# SPDX-License-Identifier: GPL-3.0-or-later
"""Original Qt Widgets interface for the independent Linux manager."""
from pathlib import Path
import os
import sys

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, QSize, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from . import NAME, VERSION
from .backend import CompilerNeeded
from . import steam

BUTTON_HEIGHT = 40
ROW_HEIGHT = 52

STYLE = """
QWidget { background: #11171d; color: #dfe7ec; font-size: 14px; }
QMainWindow, QDialog { background: #11171d; }
QFrame#card { background: #19232d; border: 1px solid #2e3b47; border-radius: 10px; }
QWidget#detailContent, QWidget#settingsContent { background: #19232d; }
QLabel { background: transparent; }
QLabel#brand { font-size: 18px; font-weight: 700; color: #ffffff; }
QLabel#gameTitle { font-size: 25px; font-weight: 700; color: #ffffff; }
QLabel#muted { color: #9daebb; }
QLabel#eyebrow { color: #5de1bc; font-size: 12px; font-weight: 700; }
QLabel#status { color: #5de1bc; font-weight: 600; }
QLabel#path { color: #aebdca; font-size: 12px; }
QLineEdit { background: #1b252f; border: 1px solid #33424f; border-radius: 7px; padding: 0 10px; }
QLineEdit:focus { border-color: #5de1bc; }
QListWidget { background: #141d25; border: 1px solid #2b3742; border-radius: 9px; padding: 5px; outline: none; }
QListWidget::item { padding: 0 11px; border-radius: 6px; }
QListWidget::item:selected { background: #263d40; color: #ffffff; }
QListWidget::item:hover { background: #21303c; }
QPushButton { background: #26323d; border: 1px solid #3b4a57; border-radius: 7px; padding: 0 12px; font-weight: 600; }
QPushButton:hover { background: #324351; border-color: #607685; }
QPushButton:focus { border-color: #5de1bc; }
QPushButton:disabled { color: #6b7c89; background: #1b252e; border-color: #293640; }
QPushButton#primary { background: #5de1bc; border-color: #5de1bc; color: #10221d; }
QPushButton#primary:hover { background: #8beed2; }
QPushButton#primary:disabled { background: #244b43; color: #83a69a; border-color: #244b43; }
QPushButton#danger { color: #efb5a9; }
QTextEdit { background: #0e151b; border: 1px solid #2e3b47; border-radius: 7px; color: #adbdc9; padding: 8px; font-size: 12px; }
QProgressBar { background: #22313c; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #5de1bc; }
QSplitter::handle { background: #11171d; width: 12px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #141d25; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #415562; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
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
    if wrap:
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return widget


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.controller = window, window.controller
        self.worker = None
        self.setWindowTitle("Settings — " + NAME)
        self.setMinimumSize(540, 420)
        self.resize(660, min(680, window.screen().availableGeometry().height() - 80))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(label("Settings", "brand"))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)
        body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        def section(title):
            frame = QFrame()
            frame.setObjectName("card")
            group = QVBoxLayout(frame)
            group.setContentsMargins(16, 16, 16, 16)
            group.setSpacing(10)
            group.addWidget(label(title, "eyebrow"))
            body_layout.addWidget(frame)
            return group

        compiler = section("SHADER COMPILER")
        self.compiler_label = label("", "muted", True)
        self.compiler_label.setMinimumWidth(0)
        self.compiler_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        compiler.addWidget(self.compiler_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.compiler_button = window.button("Choose file…", self.choose_compiler)
        self.automatic_button = window.button("Detect automatically", self.automatic_compiler)
        row.addWidget(self.compiler_button)
        row.addWidget(self.automatic_button)
        compiler.addLayout(row)
        self.update_compiler_label()

        updates = section("UPDATES")
        self.update_status = label(f"Installed launcher: {VERSION}. Check this fork for a newer Linux package.", "muted", True)
        updates.addWidget(self.update_status)
        self.check_button = window.button("Check for updates", self.check_updates)
        updates.addWidget(self.check_button)
        self.release_button = window.button("Open release page ↗", self.open_release)
        self.release_button.hide()
        updates.addWidget(self.release_button)

        about = section("ABOUT AND HELP")
        about.addWidget(label(f"{NAME} {VERSION}\nIndependent native Linux manager for the AMDNR Linux bridge. Existing Proton prefixes and saves are retained.", "muted", True))
        about.addWidget(label("Tested GPU: RX 9070 XT. See the installation guide for game and Proton validation details.", "muted", True))
        attribution = label("AMDNR by 3zwr1\nhttps://github.com/3zwr1/AMD-NR---OptiScaler", "muted", True)
        attribution.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        about.addWidget(attribution)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(window.button("Installation guide ↗", lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/hassanm0301/AMDNR-Linux/blob/main/linux/INSTALL.md"))))
        row.addWidget(window.button("Licenses and source", self.open_notices))
        about.addLayout(row)
        body_layout.addStretch()
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = window.button("Close", self.accept)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

    def update_compiler_label(self):
        self.compiler_label.setText(self.controller.settings.get("compiler") or
                                    "Find a native d3dcompiler_47.dll automatically in your installed games.")

    def choose_compiler(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select a native Windows shader compiler", str(Path.home()),
                                             "Shader compiler (d3dcompiler_47.dll D3DCompiler_47.dll)")
        if path:
            try:
                self.controller.choose_compiler(path)
                self.update_compiler_label()
            except Exception as error:
                QMessageBox.warning(self, "Could not use this compiler", str(error))

    def automatic_compiler(self):
        try:
            self.controller.settings["compiler"] = None
            self.controller.save_settings()
            self.update_compiler_label()
        except Exception as error:
            QMessageBox.warning(self, "Could not save settings", str(error))

    def check_updates(self):
        if self.worker is not None:
            return
        self.update_status.setText("Checking for updates…")
        self.release_button.hide()
        for button in (self.check_button, self.close_button, self.compiler_button, self.automatic_button):
            button.setEnabled(False)
        self.worker = Worker(self.controller, self.controller.check_updates)
        self.worker.done.connect(self.updates_ready)
        self.worker.failed.connect(self.update_failed)
        self.worker.finished.connect(self.update_finished)
        self.worker.start()

    @Slot(object)
    def updates_ready(self, result):
        if result:
            self.update_status.setText(f"Linux package {result['version']} is available. Close Settings, select an installed game and choose Update. Download a new portable archive to update the launcher itself.")
            self.release_button.show()
        else:
            self.update_status.setText("This launcher includes the latest published Linux package.")

    @Slot(object)
    def update_failed(self, error):
        self.update_status.setText("Could not check for updates. " + str(error))

    @Slot()
    def update_finished(self):
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        for button in (self.check_button, self.close_button, self.compiler_button, self.automatic_button):
            button.setEnabled(True)

    def open_release(self):
        QDesktopServices.openUrl(QUrl("https://github.com/hassanm0301/AMDNR-Linux/releases"))

    def open_notices(self):
        portable = os.environ.get("NRL_PORTABLE_ROOT")
        target = Path(portable) / "RUNTIME-LICENSES.md" if portable else Path(__file__).with_name("RUNTIME-LICENSES.md")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def reject(self):
        if self.worker is None:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)


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
        self.resize(1100, 720)
        self.setMinimumSize(800, 600)
        outer = QWidget()
        self.setCentralWidget(outer)
        content = QVBoxLayout(outer)
        content.setContentsMargins(18, 16, 18, 16)
        content.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(label(NAME, "brand"))
        header.addWidget(label(VERSION, "muted"))
        header.addStretch()
        self.add_button = self.button("Add game", self.add_game)
        self.rescan = self.button("Rescan library", self.refresh)
        self.settings_button = self.button("Settings", self.settings)
        for button in (self.add_button, self.rescan, self.settings_button):
            header.addWidget(button)
        content.addLayout(header)
        self.split = QSplitter()
        self.split.setHandleWidth(12)
        self.split.setChildrenCollapsible(False)
        left = QWidget()
        left.setMinimumWidth(230)
        left.setMaximumWidth(520)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        self.search = QLineEdit()
        self.search.setFixedHeight(BUTTON_HEIGHT)
        self.search.setPlaceholderText("Search installed games…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_games)
        left_layout.addWidget(self.search)
        self.library = QListWidget()
        self.library.setUniformItemSizes(True)
        self.library.setSpacing(3)
        self.library.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.library.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.library.currentItemChanged.connect(self.select_game)
        left_layout.addWidget(self.library, 1)
        self.empty_library = label("No installed games found.\nAdd a game or rescan your library.", "muted", True)
        self.empty_library.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_library.hide()
        left_layout.addWidget(self.empty_library, 1)
        self.count = label("Finding your library…", "muted")
        left_layout.addWidget(self.count)
        self.split.addWidget(left)
        self.details = QFrame()
        self.details.setObjectName("card")
        self.details.setMinimumWidth(400)
        frame_layout = QVBoxLayout(self.details)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_content = QWidget()
        self.detail_content.setObjectName("detailContent")
        detail = QVBoxLayout(self.detail_content)
        detail.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        detail.setContentsMargins(22, 22, 22, 22)
        detail.setSpacing(12)
        self.platform = label("LIBRARY", "eyebrow")
        self.title = label("Choose a game", "gameTitle", True)
        self.status = label("Select a game to see its installation.", "status", True)
        self.description = label("Installed Steam games appear automatically. You can also add a game from another library.", "muted", True)
        self.path = label("", "path", True)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.platform, self.title, self.status, self.description, self.path):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            detail.addWidget(widget)
        self.browse = self.button("Choose game executable…", self.choose_executable)
        detail.addWidget(self.browse)
        self.actions = QWidget()
        self.actions.setObjectName("detailContent")
        actions = QVBoxLayout(self.actions)
        actions.setContentsMargins(0, 8, 0, 0)
        actions.setSpacing(8)
        self.install = self.button("Install neural rendering", lambda: self.operate("install"), "primary")
        self.launch = self.button("Launch in Steam ↗", self.launch_game)
        self.repair = self.button("Repair", lambda: self.operate("repair"))
        self.update = self.button("Update", lambda: self.operate("update"))
        self.folder = self.button("Game folder", self.open_folder)
        self.logs = self.button("Save logs", self.save_logs)
        actions.addWidget(self.install)
        actions.addWidget(self.launch)
        for buttons in ((self.repair, self.update), (self.folder, self.logs)):
            row = QHBoxLayout()
            row.setSpacing(8)
            for button in buttons:
                row.addWidget(button)
            actions.addLayout(row)
        self.uninstall = self.button("Uninstall and restore originals", lambda: self.operate("uninstall"), "danger")
        actions.addWidget(self.uninstall)
        detail.addWidget(self.actions)
        self.install_hint = label("Close the game and Steam before changing its installation.", "muted", True)
        detail.addWidget(self.install_hint)
        detail.addStretch(1)
        self.detail_scroll.setWidget(self.detail_content)
        frame_layout.addWidget(self.detail_scroll)
        self.split.addWidget(self.details)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([340, 700])
        content.addWidget(self.split, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        content.addWidget(self.progress)
        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.activity = label("Ready", "muted", True)
        self.activity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        footer.addWidget(self.activity, 1)
        self.log_toggle = self.button("Show activity", self.toggle_log)
        footer.addWidget(self.log_toggle)
        content.addLayout(footer)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        self.log.hide()
        content.addWidget(self.log)
        self.controls()

    def button(self, text, callback, name=None):
        button = QPushButton(text)
        button.setFixedHeight(BUTTON_HEIGHT)
        button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if name:
            button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def controls(self):
        selected = self.game is not None
        resolved = selected and self.game.exe_dir is not None
        ambiguous = selected and getattr(self.game, "library_ambiguous", False)
        installed = self.inspection.get("installed", False)
        for widget in (self.library, self.rescan, self.add_button, self.settings_button, self.browse, self.search):
            widget.setEnabled(not self.busy)
        self.browse.setVisible(selected)
        self.browse.setEnabled(bool(selected and not ambiguous and not self.busy))
        self.actions.setVisible(selected)
        self.install_hint.setVisible(selected)
        self.install.setEnabled(bool(resolved and not installed and not self.busy))
        for widget in (self.repair, self.update, self.uninstall):
            widget.setEnabled(bool(resolved and installed and not self.busy))
        unsafe = (ambiguous or self.inspection.get("recovery_needed") or self.inspection.get("conflicts") or
                  self.inspection.get("health_issues") or
                  (installed and (self.inspection.get("missing") or self.inspection.get("modified"))) or
                  self.controller.pending_operation(self.game)) if selected else False
        self.launch.setEnabled(bool(selected and self.game.appid and not self.busy and not unsafe))
        self.folder.setEnabled(bool(resolved and not self.busy))
        self.logs.setEnabled(bool(resolved and not self.busy))

    @Slot(str)
    def report(self, message):
        self.activity.setText(message)
        self.log.append(message.replace("&", "&amp;").replace("<", "&lt;"))

    def work(self, function, success, refresh=False):
        if self.busy:
            return
        self.busy = True
        self.refresh_after_work = refresh
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
        if getattr(self, "need_compiler", False):
            self.need_compiler = False
            self.refresh_after_work = False
            QTimer.singleShot(0, self.compiler_needed)
            return
        # Selection refresh must wait until the worker has fully stopped.
        if getattr(self, "refresh_after_work", False):
            self.refresh_after_work = False
            QTimer.singleShot(0, lambda: self.select_game(self.library.currentItem()))

    @Slot(object)
    def failure(self, error):
        self.report(str(error))
        if isinstance(error, CompilerNeeded):
            self.need_compiler = True
        else:
            QMessageBox.warning(self, "Could not complete the operation", str(error))

    def compiler_needed(self):
        QMessageBox.information(self, "One file is needed", "No usable shader compiler was found automatically. Select a native d3dcompiler_47.dll from an installed Windows game. Wine's built-in copy is not suitable.")
        if self.pick_compiler() and self.last_operation:
            self.operate(self.last_operation)
        else:
            self.select_game(self.library.currentItem())

    def refresh(self):
        self.work(self.controller.discover, self.show_games)

    @Slot(object)
    def show_games(self, games):
        selected = self.game
        self.library.blockSignals(True)
        self.library.clear()
        for game in games:
            item = QListWidgetItem(game.name)
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            item.setData(Qt.ItemDataRole.UserRole, game)
            item.setToolTip(str(game.install_dir))
            self.library.addItem(item)
            if selected and game.appid == selected.appid and game.install_dir == selected.install_dir:
                self.library.setCurrentItem(item)
        if self.library.currentRow() < 0 and games:
            self.library.setCurrentRow(0)
        self.library.blockSignals(False)
        self.filter_games(self.search.text())
        self.report("Library ready. Choose a game to get started.")
        self.refresh_after_work = True
        self.library_ready.emit()

    def filter_games(self, query):
        visible = []
        self.library.blockSignals(True)
        for i in range(self.library.count()):
            item = self.library.item(i)
            item.setHidden(query.casefold() not in item.text().casefold())
            if not item.isHidden():
                visible.append(item)
        if self.library.currentItem() not in visible:
            if visible:
                self.library.setCurrentItem(visible[0])
            else:
                self.library.setCurrentRow(-1)
                self.library.clearSelection()
        self.library.blockSignals(False)
        self.library.setVisible(bool(visible))
        self.empty_library.setVisible(not visible)
        self.empty_library.setText("No matching games.\nTry a different search." if self.library.count()
                                   else "No installed games found.\nAdd a game or rescan your library.")
        total = self.library.count()
        self.count.setText(f"{len(visible)} of {total} games" if query else f"{total} installed games")
        if self.busy:
            self.refresh_after_work = True
        else:
            self.select_game(self.library.currentItem())

    def select_game(self, item, previous=None):
        if self.busy:
            return
        self.game = item.data(Qt.ItemDataRole.UserRole) if item and not item.isHidden() else None
        self.inspection = {}
        self.detail_scroll.verticalScrollBar().setValue(0)
        if not self.game:
            self.platform.setText("LIBRARY")
            self.title.setText("No matching games" if self.library.count() else "Add your first game")
            self.status.setText("")
            self.description.setText("Try a different search to find an installed game." if self.library.count()
                                     else "Use Add game to choose a Windows executable, or Rescan library to check Steam again.")
            self.path.setText("")
            self.controls()
            return
        game = self.game
        self.title.setText(game.name)
        self.platform.setText(f"STEAM  /  {game.appid}" if game.appid else "MANUALLY ADDED GAME")
        self.path.setText(str(game.exe_dir or game.install_dir))
        if getattr(game, "library_ambiguous", False):
            self.status.setText("Multiple Steam copies")
            self.description.setText("Steam lists multiple copies. Resolve the library in Steam or use Add game to add the exact executable manually.")
        else:
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
            health_issues = result.get("health_issues", [])
            self.status.setText("Repair recommended" if damaged or health_issues else "Installed · Files verified")
            if damaged or health_issues:
                details = ([f"{damaged} managed files need attention."] if damaged else []) + health_issues
                self.description.setText("\n".join(details) + "\nRepair restores the installation and keeps your configuration.")
            else:
                self.description.setText("Enable DLSS, FSR or XeSS in the game. Insert opens OptiScaler; Home toggles neural rendering.")
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
        self.work(lambda: self.controller.operate(action, self.game), self.operation_done, refresh=True)

    @Slot(object)
    def operation_done(self, result):
        self.report(result["message"])
        self.refresh_after_work = True
        if result.get("launch_options"):
            QMessageBox.information(self, "Manual game launch option", result["message"] + "\n\n" + result["launch_options"])

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
        if not self.busy:
            SettingsDialog(self).exec()

    def launch_game(self):
        if self.game and self.game.appid:
            QDesktopServices.openUrl(QUrl(f"steam://rungameid/{self.game.appid}"))

    def open_folder(self):
        if self.game:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.game.exe_dir or self.game.install_dir)))

    def save_logs(self):
        if self.busy or not self.game:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save logs — review before sharing", str(Path.home() / "amdnr-linux-logs.zip"), "ZIP archive (*.zip)")
        if path:
            self.work(lambda: self.controller.collect_logs(self.game, Path(path)), lambda result: self.report(f"Logs saved to {result}. Review personal paths before sharing."))

    def toggle_log(self):
        self.log.setVisible(not self.log.isVisible())
        self.log_toggle.setText("Hide activity" if self.log.isVisible() else "Show activity")

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
