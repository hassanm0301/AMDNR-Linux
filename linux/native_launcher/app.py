# SPDX-License-Identifier: GPL-3.0-or-later
"""Original Qt Widgets interface for the independent Linux manager."""
from pathlib import Path, PurePosixPath
import os
import sys

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, QSize, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from . import NAME, VERSION
from .backend import CompilerNeeded
from . import steam
from . import stores
from .artwork import Source
from .artwork_widgets import ArtworkLoader, Banner, placeholder

BUTTON_HEIGHT = 40
ROW_HEIGHT = 52

STYLE = """
QWidget { background: #11171d; color: #dfe7ec; font-size: 14px; }
QMainWindow, QDialog { background: #11171d; }
QFrame#card { background: #19232d; border: 1px solid #2e3b47; border-radius: 10px; }
QWidget#detailContent, QWidget#settingsContent, QWidget#proxyPanel, QWidget#componentPanel { background: #19232d; }
QLabel { background: transparent; }
QLabel#brand { font-size: 18px; font-weight: 700; color: #ffffff; }
QLabel#gameTitle { font-size: 25px; font-weight: 700; color: #ffffff; }
QLabel#muted { color: #9daebb; }
QLabel#eyebrow { color: #5de1bc; font-size: 12px; font-weight: 700; }
QLabel#status { color: #5de1bc; font-weight: 600; }
QLabel#path { color: #aebdca; font-size: 12px; }
QLabel#notice { color: #f0c987; }
QCheckBox { background: transparent; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #607685; border-radius: 4px; background: #1b252f; }
QCheckBox::indicator:checked { background: #5de1bc; border-color: #5de1bc; }
QCheckBox::indicator:disabled { border-color: #293640; background: #19232d; }
QLineEdit { background: #1b252f; border: 1px solid #33424f; border-radius: 7px; padding: 0 10px; }
QLineEdit:focus { border-color: #5de1bc; }
QComboBox { background: #1b252f; border: 1px solid #33424f; border-radius: 7px; padding: 0 10px; }
QComboBox QAbstractItemView { background: #1b252f; selection-background-color: #263d40; }
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
        cache = getattr(controller, "cache_dir", Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nr-linux-manager")
        self.artwork = ArtworkLoader(cache / "artwork", self)
        self.artwork.ready.connect(self.artwork_ready)
        self.artwork_key = None
        self.placeholder_icon = QIcon(placeholder())
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
        self.library.setIconSize(QSize(76, 38))
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
        self.banner = Banner()
        self.banner.hide()
        detail.addWidget(self.banner)
        self.platform = label("LIBRARY", "eyebrow")
        self.title = label("Choose a game", "gameTitle", True)
        self.status = label("Select a game to see its installation.", "status", True)
        self.description = label("Installed Steam games appear automatically. You can also add a game from another library.", "muted", True)
        self.path = label("", "path", True)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notices = label("", "notice", True)
        self.notices.hide()
        for widget in (self.platform, self.title, self.status, self.description, self.notices, self.path):
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
        self.clean = self.button("Remove leftover AMDNR files", lambda: self.operate("clean"), "danger")
        actions.addWidget(self.clean)
        self.component_panel = QWidget()
        self.component_panel.setObjectName("componentPanel")
        component_layout = QVBoxLayout(self.component_panel)
        component_layout.setContentsMargins(0, 8, 0, 0)
        component_layout.setSpacing(8)
        component_layout.addWidget(label("NEURAL RUNTIME AND PLUGINS", "eyebrow"))
        self.host_choice = QComboBox()
        self.host_choice.setFixedHeight(BUTTON_HEIGHT)
        self.host_choice.setAccessibleName("OptiScaler host build")
        self.host_choice.currentIndexChanged.connect(self.host_changed)
        component_layout.addWidget(self.host_choice)
        self.runtime_choice = QComboBox()
        self.runtime_choice.setFixedHeight(BUTTON_HEIGHT)
        self.runtime_choice.setAccessibleName("Neural runtime")
        self.runtime_choice.currentIndexChanged.connect(self.controls)
        component_layout.addWidget(self.runtime_choice)
        self.optipatcher_check = QCheckBox("OptiPatcher plugin (OptiScaler project)")
        self.optipatcher_check.toggled.connect(self.controls)
        component_layout.addWidget(self.optipatcher_check)
        self.model_e_check = QCheckBox("Model E upscaler: HelixSR by lonewolf0622 (experimental)")
        self.model_e_check.toggled.connect(self.model_e_toggled)
        component_layout.addWidget(self.model_e_check)
        self.component_note = label("", "muted", True)
        component_layout.addWidget(self.component_note)
        self.components_apply = self.button("Apply runtime, plugin and upscaler", self.apply_components)
        component_layout.addWidget(self.components_apply)
        actions.addWidget(self.component_panel)
        self.component_panel.hide()
        self.proxy_toggle = self.button("Advanced compatibility ▾", self.toggle_proxy)
        actions.addWidget(self.proxy_toggle)
        self.proxy_panel = QWidget()
        self.proxy_panel.setObjectName("proxyPanel")
        proxy_layout = QVBoxLayout(self.proxy_panel)
        proxy_layout.setContentsMargins(0, 0, 0, 0)
        proxy_layout.setSpacing(8)
        self.proxy_summary = label("", "muted", True)
        proxy_layout.addWidget(self.proxy_summary)
        self.proxy_choice = QComboBox()
        self.proxy_choice.setFixedHeight(BUTTON_HEIGHT)
        self.proxy_choice.setAccessibleName("Loading DLL")
        self.proxy_choice.currentIndexChanged.connect(self.controls)
        proxy_layout.addWidget(self.proxy_choice)
        proxy_buttons = QHBoxLayout()
        self.proxy_apply = self.button("Use selected DLL", lambda: self.operate("switch_proxy"))
        self.proxy_next = self.button("Try next DLL", lambda: self.operate("next_proxy"))
        proxy_buttons.addWidget(self.proxy_apply)
        proxy_buttons.addWidget(self.proxy_next)
        proxy_layout.addLayout(proxy_buttons)
        proxy_layout.addWidget(label("If OptiScaler does not open, try another loading DLL, then launch the game again. Names used by other files are unavailable.", "muted", True))
        actions.addWidget(self.proxy_panel)
        self.proxy_panel.hide()
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
        # Leftovers replace Uninstall, which needs an installation record.
        leftovers = bool(resolved and not installed and self.leftover_count())
        self.clean.setVisible(leftovers)
        self.uninstall.setVisible(not leftovers)
        self.clean.setEnabled(bool(leftovers and not self.busy and not ambiguous and not self.inspection.get("running")
                                   and not self.inspection.get("recovery_needed")
                                   and not self.inspection["leftovers"].get("issues")
                                   and not self.controller.pending_operation(self.game)))
        store = stores.store_name(self.game) if selected else None
        launch_issue = stores.launch_issue(self.game, installed) if store else None
        self.launch.setText(f"Launch in {store or 'Steam'} ↗")
        self.launch.setToolTip(launch_issue or "")
        self.launch.setEnabled(bool(selected and (self.game.appid or (store and not launch_issue))
                                    and not self.busy and not unsafe))
        self.folder.setEnabled(bool(resolved and not self.busy))
        self.logs.setEnabled(bool(resolved and not self.busy))
        can_choose = bool(resolved and not ambiguous and not self.busy and not self.inspection.get("running")
                          and not self.inspection.get("conflicts") and not self.inspection.get("recovery_needed")
                          and not self.controller.pending_operation(self.game))
        self.proxy_toggle.setEnabled(bool(resolved and not self.busy))
        self.proxy_choice.setEnabled(can_choose)
        choice = self.proxy_choice.currentData()
        can_switch = can_choose and installed and not unsafe and self.inspection.get("proxy_switch_supported", False)
        self.proxy_apply.setEnabled(bool(can_switch and choice and choice not in ("auto", self.inspection.get("proxy_name"))))
        self.proxy_next.setEnabled(bool(can_switch and self.inspection.get("proxy_next")))
        choices = self.inspection.get("component_choices") or {}
        self.component_panel.setVisible(bool(resolved and choices.get("supported")))
        for widget in (self.host_choice, self.runtime_choice, self.optipatcher_check):
            widget.setEnabled(can_choose)
        model_e = choices.get("model_e") or {}
        self.model_e_check.setEnabled(bool(can_choose and model_e.get("gpu_supported", True)
                                           and self.selected_host()["key"] == "amdnr"))
        self.components_apply.setVisible(bool(installed))
        self.components_apply.setEnabled(bool(can_choose and installed and not unsafe
                                              and self.selected_components() != self.installed_components()))

    def leftover_count(self):
        """How many things the last inspection found for Remove leftover AMDNR files to change."""
        found = self.inspection.get("leftovers") or {}
        return (sum(len(found.get(key) or ()) for key in ("remove", "restore", "folders", "prefix"))
                + bool(found.get("steam_wrapper")))

    def leftover_text(self):
        found = self.inspection.get("leftovers") or {}
        count = len(found.get("remove") or ()) + len(found.get("folders") or ())
        text = "AMDNR is not installed here, but an earlier installation or its game sessions left "
        text += f"{count} item{'s' if count != 1 else ''} behind." if count else "changes behind."
        if found.get("restore") or found.get("prefix"):
            text += " Original files it replaced are still in its backups."
        linked = self.linked_folders()
        if linked:
            count = sum(linked.values())
            text += (f" {count} {'are links' if count != 1 else 'is a link'} to AMDNR files in "
                     f"{' and '.join(linked)}, probably a mod manager's folder; those files are removed too.")
        text += " Remove leftover AMDNR files lists every change before it starts."
        if found.get("steam_wrapper"):
            text += "\nSteam's launch options still start this game through the AMDNR wrapper."
        if found.get("issues"):
            text += "\nThey cannot be removed yet:\n" + "\n".join(found["issues"])
        return text

    def linked_folders(self):
        """{folder: count} of the AMDNR files leftover links point to, by the folder mirroring the game's."""
        found = self.inspection.get("leftovers") or {}
        folders = {}
        for name, target in (found.get("linked") or {}).items():
            folder = str(Path(target).parents[len(PurePosixPath(name).parts) - 1])
            folders[folder] = folders.get(folder, 0) + 1
        return dict(sorted(folders.items()))

    def confirm_clean(self):
        found = self.inspection.get("leftovers") or {}
        groups = {}
        for name in found.get("remove") or ():
            top, _, rest = name.partition("/")
            groups.setdefault(top + "/" if rest else top, []).append(name)
        # Files outside the game folder come first, so the list never cuts them off.
        items = [f"Delete the {count} AMDNR file{'s' if count != 1 else ''} the links below point to, in {folder}"
                 for folder, count in self.linked_folders().items()]
        items += [f"Remove {key} ({len(names)} file{'s' if len(names) != 1 else ''})" if key.endswith("/")
                  else f"Remove {key}" for key, names in groups.items()]
        items += [f"Remove the empty {name}/ folder" for name in found.get("folders") or ()]
        items += [f"Put back the original {name}" for name in found.get("restore") or ()]
        items += [f"Put back the original {item['path']}" for item in found.get("prefix") or ()]
        if found.get("steam_wrapper"):
            items.append("Take the AMDNR wrapper out of Steam's launch options")
        shown, kept = items[:15], found.get("kept") or []
        text = (f"Clean up after AMDNR in {self.game.name}?\n\n" + "\n".join("• " + item for item in shown)
                + (f"\n• …and {len(items) - len(shown)} more" if len(items) > len(shown) else ""))
        if kept:
            text += "\n\nLeft as they are:\n" + "\n".join(f"• {item['path']}: {item['reason']}" for item in kept[:5])
            text += f"\n• …and {len(kept) - 5} more" if len(kept) > 5 else ""
        text += "\n\nYour saves are kept."
        if found.get("save_config"):
            text += " The current OptiScaler.ini is first copied to the launcher's data folder."
        answer = QMessageBox.question(self, "Remove leftover AMDNR files?", text)
        return answer == QMessageBox.StandardButton.Yes

    def selected_components(self):
        plugins =(self.inspection.get("component_choices") or {}).get("optipatcher") or [None]
        selection = {"daniel": self.runtime_choice.currentData(),
                     "optipatcher": plugins[0] if self.optipatcher_check.isChecked() else None}
        if self.host_choice.currentData() not in (None, "amdnr"):
            selection["host"] = self.host_choice.currentData()
        elif self.model_e_check.isChecked() and (self.inspection.get("component_choices") or {}).get("model_e"):
            selection["model_e"] = True
        return selection

    def installed_components(self):
        installed = self.inspection.get("components") or {}
        selection = {"daniel": (installed.get("daniel") or {}).get("version"),
                     "optipatcher": (installed.get("optipatcher") or {}).get("version")}
        if (installed.get("host") or {}).get("build"):
            selection["host"] = installed["host"]["build"]
        if installed.get("model_e"):
            selection["model_e"] = True
        return selection

    def model_e_notice(self, choice):
        """The disclosure shown once before Model E can be chosen."""
        return (
            f"Model E is HelixSR {choice['helixsr']} by lonewolf0622 ({choice['homepage']}), an upscaler that runs "
            "NVIDIA's DLSS network as Direct3D 12 shaders. It is neither AMDNR's work nor this launcher's, and it is "
            "used under the HelixSR Freeware License.\n\n"
            "To set it up, this launcher will:\n"
            f"• download HelixSR {choice['helixsr']} from its author's GitHub release and Microsoft's DirectX shader "
            "compiler from Microsoft's GitHub;\n"
            f"• download NVIDIA's DLSS {choice['dlss']} DLL from NVIDIA's GitHub. NVIDIA's licence applies: "
            f"{choice['dlss_license']};\n"
            "• run HelixSR's own setup scripts on this PC to build HelixSR's network from that DLL. This needs Python 3 "
            "with numpy, and Proton or Wine for the shader compiler; it takes about a minute.\n\n"
            "NVIDIA's DLL is deleted after the build. The built files contain NVIDIA's network: they stay on this PC. "
            "Do not share or upload them.\n\n"
            "In the game Model E runs where FSR 3.1 or FSR 4 would (D3D12 games only). On Linux it has passed "
            "automated tests only; no game report yet.\n\nAccept and continue?")

    def model_e_toggled(self, checked):
        choice = (self.inspection.get("component_choices") or {}).get("model_e") or {}
        if checked and not choice.get("accepted"):
            answer = QMessageBox.question(self, "Model E (HelixSR)", self.model_e_notice(choice))
            accepted = answer == QMessageBox.StandardButton.Yes
            if accepted:
                try:
                    self.controller.accept_model_e()
                    choice["accepted"] = True
                except Exception as error:
                    accepted = False
                    self.failure(error)
            if not accepted:
                self.model_e_check.blockSignals(True)
                self.model_e_check.setChecked(False)
                self.model_e_check.blockSignals(False)
        self.controls()

    def selected_host(self):
        choices = self.inspection.get("component_choices") or {}
        hosts = {item["key"]: item for item in choices.get("hosts") or []}
        return hosts.get(self.host_choice.currentData() or "amdnr", {"key": "amdnr", "builtin_runtime": True})

    def fill_runtimes(self, daniel):
        """List the runtimes the selected host drives; only AMDNR has a built-in one."""
        choices = self.inspection.get("component_choices") or {}
        host = self.selected_host()
        self.runtime_choice.blockSignals(True)
        self.runtime_choice.clear()
        if host.get("builtin_runtime", True):
            self.runtime_choice.addItem("lmxxf, built into AMDNR (tested in games on Linux)", None)
        for version in (choices.get("host_runtimes") or {}).get(host["key"], choices.get("runtimes", [])):
            self.runtime_choice.addItem(f"danielblnc {version}, downloaded from AMDNR's release", version)
        index = self.runtime_choice.findData(daniel)
        self.runtime_choice.setCurrentIndex(max(0, index))
        self.runtime_choice.blockSignals(False)

    def host_changed(self):
        self.fill_runtimes(self.runtime_choice.currentData())
        self.component_note.setText(self.component_text())
        self.controls()

    def component_text(self):
        choices = self.inspection.get("component_choices") or {}
        gpu = choices.get("gpu") or {}
        host = self.selected_host()
        gpu_text = (f"GPU: {gpu['gfx']} ({gpu['generation'].upper()})" if gpu.get("gfx")
                    else "GPU generation not detected; all danielblnc runtimes this host drives are listed.")
        if gpu.get("apu"):
            gpu_text += ". danielblnc's runtime does not run on integrated GPUs."
        text = (gpu_text + "\nOptiPatcher patches the games it supports so they offer upscaler inputs on AMD."
                "\ndanielblnc's runtime is DLSS-NR on AMD by Daniel Blanco, shipped unmodified by AMDNR with his "
                "permission; all rights stay with him. On Linux it passes automated GPU tests; game reports are still needed. "
                "Switching runtimes later rewrites only [DlssNr] NrBackend in OptiScaler.ini.")
        if host["key"] != "amdnr":
            text += (f"\n{host.get('title', host['key'])} is an independent OptiScaler fork by {host.get('author', 'its author')} "
                     "(GPL-3.0) that replaces the AMDNR host and runs danielblnc's runtime only. Its installer scripts "
                     "are not used. It passes the same automated host test on Linux; no game report yet.")
            if not (choices.get("host_runtimes") or {}).get(host["key"]):
                text += " No runtime it drives supports this GPU."
        if len(choices.get("hosts") or []) > 1:
            text += ("\nChanging the host build replaces OptiScaler.ini with that host's own; the previous file is "
                     "kept in the launcher's state folder.")
        model_e = choices.get("model_e")
        if model_e:
            text += ("\nModel E is HelixSR, an upscaler by lonewolf0622 that replaces FSR 3.1/4 in D3D12 games. "
                     "It is built once on this PC from NVIDIA's DLSS DLL" + (" (already built)." if model_e.get("built") else ".")
                     + (" It needs the AMDNR host." if host["key"] != "amdnr" else "")
                     + ("" if model_e.get("gpu_supported", True) else " It needs an RDNA 1 or newer GPU."))
        return text

    def show_components(self, result):
        choices = result.get("component_choices") or {}
        selected = choices.get("selected") or {}
        self.host_choice.blockSignals(True)
        self.optipatcher_check.blockSignals(True)
        self.host_choice.clear()
        for host in choices.get("hosts") or []:
            self.host_choice.addItem(f"{host['title']} (default)" if host["key"] == "amdnr"
                                     else f"{host['title']} by {host.get('author', 'its author')}", host["key"])
        self.host_choice.setCurrentIndex(max(0, self.host_choice.findData(selected.get("host") or "amdnr")))
        self.host_choice.setVisible(self.host_choice.count() > 1)
        self.fill_runtimes(selected.get("daniel"))
        self.optipatcher_check.setChecked(bool(selected.get("optipatcher")))
        self.optipatcher_check.setVisible(bool(choices.get("optipatcher")))
        self.model_e_check.blockSignals(True)
        self.model_e_check.setChecked(bool(selected.get("model_e")))
        self.model_e_check.setVisible(bool(choices.get("model_e")))
        self.model_e_check.blockSignals(False)
        self.host_choice.blockSignals(False)
        self.optipatcher_check.blockSignals(False)
        self.component_note.setText(self.component_text())

    def confirm_notices(self):
        """Ask before changing files in a game with a blocking notice."""
        texts = [item["text"] for item in self.inspection.get("notices", []) if item.get("level") == "confirm"]
        if not texts:
            return True
        answer = QMessageBox.question(self, "Install anyway?", "\n\n".join(texts) + "\n\nInstall anyway?")
        return answer == QMessageBox.StandardButton.Yes

    def save_components(self):
        if not hasattr(self.controller, "set_components") or not self.component_panel.isVisible():
            return True
        try:
            self.controller.set_components(self.game, **self.selected_components())
            return True
        except Exception as error:
            self.failure(error)
            return False

    def apply_components(self):
        if self.busy or not self.game or not self.save_components():
            return
        current = getattr(getattr(self.controller, "backend", None), "addon_version", None)
        self.operate("repair" if self.inspection.get("addon_version") == current else "update")

    def toggle_proxy(self):
        self.proxy_panel.setVisible(not self.proxy_panel.isVisible())
        self.proxy_toggle.setText("Advanced compatibility ▴" if self.proxy_panel.isVisible() else "Advanced compatibility ▾")
        if self.proxy_panel.isVisible():
            QTimer.singleShot(0, lambda: self.detail_scroll.ensureWidgetVisible(self.proxy_next))

    def show_proxies(self, result):
        self.proxy_choice.blockSignals(True)
        self.proxy_choice.clear()
        self.proxy_choice.addItem("Automatic (recommended)", "auto")
        current = result.get("proxy_name")
        options = result.get("proxy_options", [])
        for option in options:
            text = option["name"]
            if option.get("current"):
                text += " · installed"
            elif not option.get("available"):
                text += " · occupied"
            self.proxy_choice.addItem(text, option["name"])
            self.proxy_choice.model().item(self.proxy_choice.count() - 1).setEnabled(bool(option.get("available")))
        index = self.proxy_choice.findData(current)
        self.proxy_choice.setCurrentIndex(max(0, index))
        self.proxy_choice.blockSignals(False)
        if current:
            tried = ", ".join(result.get("proxy_history", []))
            issue = result.get("proxy_switch_issue")
            self.proxy_summary.setText(f"Installed loading DLL: {current}" + (f"\nTried: {tried}" if tried else "")
                                       + (f"\n{issue}" if issue else "")
                                       + ("\nNo untried names remain. You can select an earlier name manually."
                                          if not issue and not result.get("proxy_next") and len(result.get("proxy_history", [])) > 1 else ""))
        else:
            available = next((item["name"] for item in options if item.get("available")), None)
            self.proxy_summary.setText(f"Automatic choice: {available}. Applies when you install." if available
                                       else "No loading DLL name is currently available.")

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
        self.artwork.images.clear()
        self.library.blockSignals(True)
        self.library.clear()
        for game in games:
            item = QListWidgetItem(game.name)
            item.setIcon(self.placeholder_icon)
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
        # Prioritize the selected banner before queueing the library thumbnails.
        current = self.library.currentItem()
        if current:
            self.request_artwork(current.data(Qt.ItemDataRole.UserRole), "banner")
        for game in games:
            self.request_artwork(game, "thumbnail")
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
        self.show_proxies({})
        self.notices.hide()
        self.detail_scroll.verticalScrollBar().setValue(0)
        if not self.game:
            self.artwork_key = None
            self.banner.set_image(None)
            self.banner.hide()
            self.platform.setText("LIBRARY")
            self.title.setText("No matching games" if self.library.count() else "Add your first game")
            self.status.setText("")
            self.description.setText("Try a different search to find an installed game." if self.library.count()
                                     else "Use Add game to choose a Windows executable, or Rescan library to check Steam again.")
            self.path.setText("")
            self.controls()
            return
        game = self.game
        source = self.artwork_source(game)
        self.artwork_key = source.key
        self.banner.set_image(None)
        self.banner.show()
        self.artwork.request(source, "banner")
        self.title.setText(game.name)
        self.platform.setText(f"STEAM  /  {game.appid}" if game.appid else stores.label(game) or "MANUALLY ADDED GAME")
        self.path.setText(str(game.exe_dir or game.install_dir))
        if getattr(game, "library_ambiguous", False):
            self.status.setText("Multiple Steam copies")
            self.description.setText("Steam lists multiple copies. Resolve the library in Steam or use Add game to add the exact executable manually.")
        else:
            self.status.setText("Checking installation…" if game.exe_dir else "Choose the game executable")
            self.description.setText("" if game.exe_dir else "Several executable locations may exist. Choose the main 64-bit DirectX 12 game executable to continue.")
        self.controls()
        if game.exe_dir:
            self.work(lambda: self.controller.inspect(game), self.show_inspection)

    def artwork_source(self, game):
        # The selected executable is already recorded by Add game / Choose exe.
        # Reading that setting avoids scanning a game folder on the UI thread.
        # Heroic and Lutris games otherwise use the target found during discovery.
        key = self.controller.game_key(game) if hasattr(self.controller, "game_key") else None
        executable = self.controller.settings.get("executables", {}).get(key) or getattr(game, "executable", None)
        return Source(game.appid, game.steam_root, Path(executable) if executable else None)

    def request_artwork(self, game, kind):
        self.artwork.request(self.artwork_source(game), kind)

    @Slot(str, str, object)
    def artwork_ready(self, key, kind, image):
        if kind == "banner":
            # Downloads may finish after the user selects a different game.
            if key == self.artwork_key:
                self.banner.set_image(image, key.startswith("exe:"))
        elif image is not None:
            icon = QIcon(QPixmap.fromImage(image))
            for index in range(self.library.count()):
                item = self.library.item(index)
                if self.artwork_source(item.data(Qt.ItemDataRole.UserRole)).key == key:
                    item.setIcon(icon)

    @Slot(object)
    def show_inspection(self, result):
        self.inspection = result
        self.show_proxies(result)
        self.show_components(result)
        notices = [item["text"] for item in result.get("notices", [])]
        self.notices.setText("\n".join(("⚠ " if item.get("level") != "info" else "ℹ ") + item["text"]
                                        for item in result.get("notices", [])))
        self.notices.setVisible(bool(notices))
        if result.get("recovery_needed") or self.controller.pending_operation(self.game):
            self.status.setText("Interrupted operation")
            self.description.setText("Close Steam and this game, then use Install or Repair. The launcher will recover the previous operation first.")
        elif result.get("conflicts"):
            self.status.setText("Existing files need attention")
            self.description.setText("\n".join(result["conflicts"]) + (
                "\n" + self.leftover_text() if self.leftover_count() else ""))
        elif result.get("installed"):
            damaged = len(result.get("missing", [])) + len(result.get("modified", []))
            health_issues = result.get("health_issues", [])
            self.status.setText("Repair recommended" if damaged or health_issues else "Installed · Files verified")
            if damaged or health_issues:
                details = ([f"{damaged} managed files need attention."] if damaged else []) + health_issues
                self.description.setText("\n".join(details) + "\nRepair restores the installation and keeps your configuration.")
            else:
                self.description.setText("Enable DLSS, FSR or XeSS in the game. Insert opens OptiScaler; Home toggles neural rendering.")
        elif self.leftover_count():
            self.status.setText("Leftover files found")
            self.description.setText(self.leftover_text())
        else:
            self.status.setText("Ready to install")
            store = stores.store_name(self.game)
            launch = ("sets Steam's launch options" if self.game.appid else
                      f"shows the wrapper to add in {store}" if store else
                      "shows the launch option to add to your game runner")
            self.description.setText("The launcher downloads the required runtime, finds a shader compiler, backs up "
                                     f"affected files and {launch}.")
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
                self.library.currentItem().setIcon(self.placeholder_icon)
                self.request_artwork(self.game, "thumbnail")
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
            restored = "original files and Steam launch options" if self.game.appid else "original files"
            answer = QMessageBox.question(self, "Restore this game?", f"Remove neural rendering from {self.game.name} and restore its {restored}?\n\nYour saves are kept.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        if action == "clean" and not self.confirm_clean():
            return
        if action == "install" and (not self.confirm_notices() or not self.save_components()):
            return
        self.last_operation = action
        game = self.game
        proxy = self.proxy_choice.currentData() or "auto"
        if action == "next_proxy":
            action, proxy = "switch_proxy", "next"
        options = {"proxy": proxy} if action in ("install", "switch_proxy") else {}
        self.work(lambda: self.controller.operate(action, game, **options), self.operation_done, refresh=True)

    @Slot(object)
    def operation_done(self, result):
        self.report(result["message"])
        self.refresh_after_work = True
        if result.get("launch_options"):
            QMessageBox.information(self, result.get("title", "Manual game launch option"),
                                    result["message"] + "\n\n" + result["launch_options"])

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
        if not self.game:
            return
        store = stores.store_name(self.game)
        # Heroic and Lutris start their games through their own link handlers.
        url = (f"steam://rungameid/{self.game.appid}" if self.game.appid
               else stores.launch_url(self.game) if store else None)
        if url and not QDesktopServices.openUrl(QUrl(url)):
            self.report(f"Could not open {store or 'Steam'}. Start the game from {store or 'Steam'} instead.")

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
            self.artwork.close()
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
