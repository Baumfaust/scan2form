"""Main window with two modes: ① Prepare (detect -> review -> export) and ② Fill out."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSlider,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from scan2form.core.autofill.fill import default_output, read_form_values
from scan2form.core.autofill.mapping import FormField, collect_suggestions, map_fields
from scan2form.core.autofill.session import FillSession, draft_key, form_fields_of
from scan2form.core.autofill.vault import Vault, VaultLockedError, VaultStore
from scan2form.core.detect import OpenCVDetector
from scan2form.core.export import export_pdf
from scan2form.core.ingest import IMAGE_SUFFIXES, PDF_SUFFIXES
from scan2form.core.layout import ARRANGEMENTS
from scan2form.core.model import MAX_FINE_DEG, Field, Orientation
from scan2form.core.naming import assign_names
from scan2form.core.ocr import Ocr
from scan2form.core.pipeline import (
    Document,
    ensure_unique_names,
    has_form_fields,
    link_across_pages,
    link_repeated_names,
    linked_pages,
    load_project,
    name_conflicts,
    open_fillable,
    process,
    save_project,
)
from scan2form.core.straighten import auto_angle
from scan2form.ui.commands import EditProperties, ReorientPage
from scan2form.ui.fill_panel import FillPanel
from scan2form.ui.fill_view import FillScene
from scan2form.ui.page_view import PageScene, PageView, Tool, to_pixmap
from scan2form.ui.progress_overlay import ProgressOverlay
from scan2form.ui.properties import PropertiesPanel
from scan2form.ui.vault_dialog import VaultDialog
from scan2form.ui.worker import Job

PROJECT_SUFFIX = ".s2f.json"
MAX_RECENT = 10
PREPARE, FILL = "prepare", "fill"
MODE_COLORS = {PREPARE: "#1e6edc", FILL: "#14963c"}
MODE_BANNERS = {
    PREPARE: "①  PREPARE FORM  —  find, draw and adjust the fields. "
    "Export saves a fillable PDF.     Ctrl+2 → fill it out",
    FILL: "②  FILL OUT  —  type into the green boxes or fill from your data. "
    "“Save filled copy” writes a separate PDF; the form is not changed.     Ctrl+1 → back to Prepare",
}
OPEN_FILTER = (
    "Forms and projects (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.json);;All files (*)"
)


def settings() -> QSettings:
    """Per-user app settings (Windows: registry). Tests replace this with a temporary file."""
    return QSettings("Scan2Form", "Scan2Form")


def _detectors() -> dict[str, str]:
    """Display label -> detector id. FFDNet only when its eval dependencies are installed."""
    options = {"OpenCV (built-in)": "opencv"}
    if importlib.util.find_spec("ultralytics") is not None:
        options["FFDNet-L (local eval)"] = "ffdnet-l"
        options["FFDNet-S (local eval)"] = "ffdnet-s"
    return options


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.document: Document | None = None
        self.project_path: Path | None = None
        self.scenes: list[PageScene] = []
        self.undo = QUndoStack(self)
        self.pool = QThreadPool.globalInstance()
        self._ocr: Ocr | None = None
        self._detector_cache: dict[str, object] = {}
        self._busy = False
        self.vault_store = VaultStore()
        self.mode = PREPARE
        self.fill_session: FillSession | None = None
        self.fill_scenes: list[FillScene] = []
        self._fill_signature: tuple | None = None  # document structure the session was built for
        # Typed values are written to an encrypted draft shortly after each change.
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(800)
        self._draft_timer.timeout.connect(self._flush_draft)

        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_statusbar()
        # Bound methods, not lambdas: they are disconnected when the window is destroyed.
        self.undo.cleanChanged.connect(self._on_clean_changed)
        self.undo.indexChanged.connect(self._update_panel)
        self.set_mode(PREPARE)

    # -- layout -------------------------------------------------------------
    def _build_central(self) -> None:
        self.view = PageView()
        self.placeholder = QLabel("Open or drop a PDF / scanned image here\n\nCtrl+O to open")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("font-size: 18px; color: #777;")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.view)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.banner)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.overlay = ProgressOverlay(self.stack)

    def _build_docks(self) -> None:
        self.page_list = QListWidget()
        self.page_list.setViewMode(QListView.ViewMode.IconMode)
        self.page_list.setFlow(QListView.Flow.TopToBottom)
        self.page_list.setWrapping(False)
        self.page_list.setMovement(QListView.Movement.Static)
        self.page_list.setIconSize(QSize(110, 150))
        self.page_list.setSpacing(6)
        self.page_list.setFixedWidth(150)
        self.page_list.currentRowChanged.connect(self.show_page)
        pages_dock = QDockWidget("Pages", self)
        pages_dock.setWidget(self.page_list)
        pages_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, pages_dock)

        self.panel = PropertiesPanel(self.name_is_taken, self.linked_pages)
        self.panel.setMinimumWidth(260)
        self.panel.changed.connect(self._on_property_changed)
        self.panel.deleteRequested.connect(self._delete_selected)
        self.panel.arrangeRequested.connect(self.arrange)
        self.fill_panel = FillPanel()
        self.fill_panel.setMinimumWidth(420)
        self.fill_panel.mappingChanged.connect(self._on_fill_mapping_changed)
        self.fill_panel.fieldActivated.connect(self.focus_fill_field)
        self.side_stack = QStackedWidget()
        self.side_stack.addWidget(self.panel)
        self.side_stack.addWidget(self.fill_panel)
        self.side_dock = QDockWidget("Properties", self)
        self.side_dock.setWidget(self.side_stack)
        self.side_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.side_dock)

    def _action(self, text: str, shortcut=None, slot=None, checkable: bool = False) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if slot:
            action.triggered.connect(slot)
        action.setCheckable(checkable)
        return action

    def _build_actions(self) -> None:
        self.act_open = self._action("Open…", QKeySequence.StandardKey.Open, self.open_dialog)
        self.act_close = self._action("Close", "Ctrl+W", self.close_document)
        self.act_close.setShortcuts([QKeySequence("Ctrl+W"), QKeySequence("Ctrl+F4")])
        self.act_close.setToolTip("Close this form (Ctrl+W)")
        self.act_save = self._action(
            "Save project", QKeySequence.StandardKey.Save, self.save_project
        )
        self.act_export = self._action("Export fillable PDF…", "Ctrl+E", self.export_dialog)
        # Own actions with fixed labels (the stack's actions put the command text in the
        # label, which makes the toolbar jump); the command shows up in the tooltip instead.
        self.act_undo = self._action("Undo", QKeySequence.StandardKey.Undo, self.undo.undo)
        self.act_redo = self._action("Redo", QKeySequence.StandardKey.Redo, self.undo.redo)
        self.undo.canUndoChanged.connect(self._on_can_undo)
        self.undo.canRedoChanged.connect(self._on_can_redo)
        self.undo.undoTextChanged.connect(self._on_undo_text)
        self.undo.redoTextChanged.connect(self._on_redo_text)
        self.act_undo.setEnabled(False)
        self.act_redo.setEnabled(False)
        self.act_detect = self._action("Detect fields", "F5", self.redetect)
        self.act_my_data = self._action("My data…", "Ctrl+Shift+D", self.open_my_data)
        self.act_fill_other = self._action("Fill out another PDF…", "Ctrl+Shift+F", self.fill_other)
        self.mode_group = QActionGroup(self)
        self.act_mode_prepare = self._action(
            "①  Prepare form", "Ctrl+1", lambda: self.set_mode(PREPARE), checkable=True
        )
        self.act_mode_fill = self._action(
            "②  Fill out", "Ctrl+2", lambda: self.set_mode(FILL), checkable=True
        )
        self.act_mode_prepare.setToolTip("Find, draw and adjust the form fields (Ctrl+1)")
        self.act_mode_fill.setToolTip("Type in your data and save a filled copy (Ctrl+2)")
        for action in (self.act_mode_prepare, self.act_mode_fill):
            self.mode_group.addAction(action)
        self.act_mode_prepare.setChecked(True)
        self.act_fill_profile = self._action(
            "Fill from profile", "Ctrl+Shift+P", self.fill_from_profile
        )
        self.act_fill_profile.setToolTip(
            "Put the selected profile's data into matching fields. Values you typed are kept; "
            "to replace one, delete it first or pick its source in the side panel."
        )
        self.act_fill_clear = self._action("Clear all", None, self.clear_fill_values)
        # Slots without arguments: triggered(bool) would otherwise arrive as `output`.
        self.act_save_copy = self._action("💾  Save filled copy", None, self._save_copy_clicked)
        self.act_save_copy.setToolTip(
            "Write the filled-in form as a separate PDF (Ctrl+S). After the first time, "
            "Ctrl+S updates that same copy."
        )
        self.act_save_copy_as = self._action(
            "Save filled copy as…", "Ctrl+Shift+S", self._save_copy_as_clicked
        )
        self.fill_status = QLabel()
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(140)
        self.profile_box.currentIndexChanged.connect(self._on_profile_changed)

        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for tool, label, key in (
            (Tool.SELECT, "Select", "V"),
            (Tool.BRUSH, "Brush", "B"),
            (Tool.BOX, "Box", "R"),
        ):
            action = self._action(
                f"{label} ({key})", key, lambda _=False, t=tool: self.set_tool(t), checkable=True
            )
            self.tool_group.addAction(action)
            self.tool_actions[tool] = action
        self.tool_actions[Tool.SELECT].setChecked(True)

        self.act_zoom_in = self._action(
            "Zoom in",
            QKeySequence.StandardKey.ZoomIn,
            lambda: self.view.set_zoom(self.view.zoom() * 1.25),
        )
        self.act_zoom_out = self._action(
            "Zoom out",
            QKeySequence.StandardKey.ZoomOut,
            lambda: self.view.set_zoom(self.view.zoom() / 1.25),
        )
        self.act_fit = self._action("Fit width", "Ctrl+0", self.view.fit_width)

        self.arrange_actions = {}
        for key, (label, _) in ARRANGEMENTS.items():
            action = self._action(
                label, "U" if key == "uniform" else None, lambda _=False, k=key: self.arrange(k)
            )
            self.arrange_actions[key] = action
        self.arrange_button = QToolButton()
        self.arrange_button.setText("Arrange")
        self.arrange_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.arrange_button.addActions(list(self.arrange_actions.values()))

        self.detector_box = QComboBox()
        for label, detector_id in _detectors().items():
            self.detector_box.addItem(label, detector_id)
        self.ocr_box = QCheckBox("OCR names")
        self.ocr_box.setChecked(True)

        mode_bar = self.addToolBar("Mode")
        mode_bar.setMovable(False)
        mode_bar.addActions([self.act_mode_prepare, self.act_mode_fill])
        for mode, action in ((PREPARE, self.act_mode_prepare), (FILL, self.act_mode_fill)):
            button = mode_bar.widgetForAction(action)
            button.setObjectName(f"mode_{mode}")
            button.setStyleSheet(
                "QToolButton { font-size: 14px; font-weight: 600; padding: 6px 16px; "
                "border: 2px solid #999; border-radius: 4px; margin: 2px; }"
                f"QToolButton:checked {{ background: {MODE_COLORS[mode]}; color: white; "
                f"border-color: {MODE_COLORS[mode]}; }}"
            )
        mode_bar.addSeparator()
        mode_bar.addActions([self.act_open, self.act_close])
        mode_bar.addSeparator()

        toolbar = self.prepare_bar = self.addToolBar("Prepare")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for item in (self.act_save, self.act_export, None, *self.tool_actions.values(), None,
                     self.act_undo, self.act_redo, None):  # fmt: skip
            toolbar.addSeparator() if item is None else toolbar.addAction(item)
        toolbar.addWidget(self.arrange_button)
        toolbar.addSeparator()
        toolbar.addAction(self.act_detect)
        toolbar.addWidget(self.detector_box)
        toolbar.addWidget(self.ocr_box)
        toolbar.addSeparator()
        toolbar.addAction(self.act_my_data)

        fill_bar = self.fill_bar = self.addToolBar("Fill out")
        fill_bar.setMovable(False)
        fill_bar.addWidget(QLabel(" Profile: "))
        fill_bar.addWidget(self.profile_box)
        fill_bar.addActions([self.act_fill_profile, self.act_fill_clear])
        fill_bar.addSeparator()
        fill_bar.addAction(self.act_save_copy)
        fill_bar.widgetForAction(self.act_save_copy).setStyleSheet(
            f"QToolButton {{ font-weight: 600; color: white; background: {MODE_COLORS[FILL]}; "
            "padding: 5px 12px; border-radius: 4px; margin: 2px; }"
            "QToolButton:disabled { background: #9bb; }"
        )
        fill_bar.addWidget(self.fill_status)
        fill_bar.addSeparator()
        fill_bar.addAction(self.act_my_data)
        fill_bar.addSeparator()
        fill_bar.addActions([self.act_zoom_out, self.act_zoom_in, self.act_fit])
        self._build_page_toolbar()

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.act_open)
        file_menu.addAction(self.act_close)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        for action in (self.act_save, self.act_export, self.act_save_copy, self.act_save_copy_as):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self._action("Quit", QKeySequence.StandardKey.Quit, self.close))
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addActions([self.act_undo, self.act_redo])
        edit_menu.addSeparator()
        edit_menu.addActions(list(self.tool_actions.values()))
        edit_menu.addSeparator()
        self.act_link = self._action(
            "Link repeated fields across pages", None, self.link_repeated_fields
        )
        self.act_link.setToolTip("Fields with the same label on several pages (vorname, vorname_2 …) "
                                 "become one field: type it once, it appears everywhere")  # fmt: skip
        edit_menu.addAction(self.act_link)
        arrange_menu = self.menuBar().addMenu("&Arrange")
        arrange_menu.addActions(list(self.arrange_actions.values()))
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addActions([self.act_mode_prepare, self.act_mode_fill])
        view_menu.addSeparator()
        view_menu.addActions([self.act_zoom_in, self.act_zoom_out, self.act_fit])
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addActions(
            [self.act_my_data, self.act_fill_profile, self.act_save_copy, self.act_fill_other]
        )

    def _build_statusbar(self) -> None:
        self.status_text = QLabel()
        self.zoom_label = QLabel()
        self.count_label = QLabel()
        self.progress = QProgressBar()
        self.progress.setMinimumWidth(300)
        self.progress.hide()
        bar = self.statusBar()
        bar.addWidget(self.status_text, 1)
        bar.addPermanentWidget(self.progress)
        bar.addPermanentWidget(self.count_label)
        bar.addPermanentWidget(self.zoom_label)
        self.view.zoomChanged.connect(self._on_zoom_changed)

    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100 / (96 / 72):.0f} %")  # 100 % = real paper size

    def _on_can_undo(self, can: bool) -> None:
        self.act_undo.setEnabled(can and self.mode == PREPARE and not self._busy)

    def _on_can_redo(self, can: bool) -> None:
        self.act_redo.setEnabled(can and self.mode == PREPARE and not self._busy)

    def _on_undo_text(self, text: str) -> None:
        self.act_undo.setToolTip(f"Undo {text}" if text else "Undo")

    def _on_redo_text(self, text: str) -> None:
        self.act_redo.setToolTip(f"Redo {text}" if text else "Redo")

    def _on_clean_changed(self, clean: bool) -> None:
        if self.mode == PREPARE:
            self.setWindowModified(not clean)

    def _delete_selected(self) -> None:
        scene = self.current_scene()
        if scene is not None:
            scene.delete_selected()

    # -- state helpers --------------------------------------------------------
    def message(self, text: str) -> None:
        self.status_text.setText(text)

    def current_scene(self) -> PageScene | None:
        return self.view.page_scene()

    def _update_title(self) -> None:
        name = self.document.source.name if self.document else "no document"
        mode = "Fill out" if self.mode == FILL else "Prepare form"
        self.setWindowTitle(f"Scan2Form – {name} – {mode}[*]")

    def _update_enabled(self) -> None:
        has_doc = self.document is not None and not self._busy
        preparing = has_doc and self.mode == PREPARE
        filling = has_doc and self.mode == FILL
        for action in (self.act_save, self.act_export, self.act_detect, self.act_link, *self.tool_actions.values(),
                       self.act_rotate_left, self.act_rotate_right, self.act_apply_all, self.act_grid):  # fmt: skip
            action.setEnabled(preparing)
        self.act_undo.setEnabled(preparing and self.undo.canUndo())
        self.act_redo.setEnabled(preparing and self.undo.canRedo())
        for action in (self.act_fill_profile, self.act_fill_clear, self.act_save_copy,
                       self.act_save_copy_as):  # fmt: skip
            action.setEnabled(filling)
        for action in (self.act_zoom_in, self.act_zoom_out, self.act_fit,
                       self.act_mode_prepare, self.act_mode_fill):  # fmt: skip
            action.setEnabled(has_doc)
        self.act_open.setEnabled(not self._busy)
        self.act_close.setEnabled(has_doc)
        self.view.setEnabled(not self._busy)
        self._sync_page_controls()

    # -- page orientation ---------------------------------------------------------
    def _build_page_toolbar(self) -> None:
        self.act_rotate_left = self._action(
            "↺ 90°", "Ctrl+Shift+Left", lambda: self.rotate_page(-1)
        )
        self.act_rotate_left.setToolTip("Turn the page 90° counter-clockwise (Ctrl+Shift+←)")
        self.act_rotate_right = self._action(
            "↻ 90°", "Ctrl+Shift+Right", lambda: self.rotate_page(1)
        )
        self.act_rotate_right.setToolTip("Turn the page 90° clockwise (Ctrl+Shift+→)")
        self.fine_slider = QSlider(Qt.Orientation.Horizontal)
        self.fine_slider.setRange(-int(MAX_FINE_DEG * 10), int(MAX_FINE_DEG * 10))
        self.fine_slider.setFixedWidth(220)
        self.fine_spin = QDoubleSpinBox()
        self.fine_spin.setRange(-MAX_FINE_DEG, MAX_FINE_DEG)
        self.fine_spin.setSingleStep(0.1)
        self.fine_spin.setDecimals(1)
        self.fine_spin.setSuffix(" °")
        self.act_auto = self._action("Auto", None, self.auto_straighten_page)
        self.act_auto.setToolTip("Measure the tilt of this page again and straighten it")
        self.act_apply_all = self._action("Apply to all pages", None, self.apply_orientation_to_all)
        self.act_grid = self._action(
            "Grid", "G", lambda checked: self.view.set_grid(checked), checkable=True
        )
        self.act_grid.setToolTip("Show a 10 mm grid to check the straightening against (G)")
        self.fine_slider.valueChanged.connect(lambda v: self.set_fine_angle(v / 10))
        self.fine_spin.valueChanged.connect(self.set_fine_angle)
        self.fine_slider.sliderPressed.connect(self._fine_drag_started)
        self.fine_slider.sliderReleased.connect(
            lambda: self.view.set_grid(self.act_grid.isChecked())
        )
        self._merge_session = 0

        bar = self.page_bar = QToolBar("Page")
        bar.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(bar)
        bar.addWidget(QLabel(" Page: "))
        bar.addActions([self.act_rotate_left, self.act_rotate_right])
        bar.addSeparator()
        self.fine_label = QLabel(" Straighten: ")
        bar.addWidget(self.fine_label)
        bar.addWidget(self.fine_slider)
        bar.addWidget(self.fine_spin)
        bar.addActions([self.act_auto, self.act_grid])
        bar.addSeparator()
        bar.addAction(self.act_apply_all)
        bar.addSeparator()
        bar.addActions([self.act_zoom_out, self.act_zoom_in, self.act_fit])

    def _sync_page_controls(self) -> None:
        """Show the current page's angle; fine controls only for scanned pages."""
        scene = self.current_scene() if hasattr(self, "fine_slider") else None
        scanned = (
            scene is not None and scene.page.scanned and not self._busy and self.mode == PREPARE
        )
        angle = scene.page.orientation.straighten_deg if scene is not None else 0.0
        for widget, value in ((self.fine_slider, round(angle * 10)), (self.fine_spin, angle)):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        tip = ("Straighten a crooked scan (± 10°). Drag and compare with the grid."
               if scanned else "Only scanned pages can be straightened; digital pages turn in 90° steps.")  # fmt: skip
        for widget in (self.fine_slider, self.fine_spin, self.fine_label):
            widget.setEnabled(scanned)
            widget.setToolTip(tip)
        self.act_auto.setEnabled(scanned)

    def _fine_drag_started(self) -> None:
        self._merge_session += 1  # one drag = one undo step
        self.view.set_grid(True)

    def _reorient(self, scene: PageScene, new: Orientation, mergeable: bool = False) -> None:
        old = scene.page.orientation
        new = new.normalized()
        if new != old:
            self.undo.push(ReorientPage(scene, old, new, mergeable, self._merge_session))

    def rotate_page(self, quarter_turns: int) -> None:
        scene = self.current_scene()
        if scene is not None:
            o = scene.page.orientation
            self._merge_session += 1
            self._reorient(scene, Orientation(o.quarter_turns + quarter_turns, o.straighten_deg))

    def set_fine_angle(self, degrees: float) -> None:
        scene = self.current_scene()
        if scene is not None and scene.page.scanned:
            self._reorient(
                scene, Orientation(scene.page.orientation.quarter_turns, degrees), mergeable=True
            )

    def auto_straighten_page(self) -> None:
        scene = self.current_scene()
        if scene is not None and scene.page.scanned:
            self._merge_session += 1
            self._reorient(
                scene, Orientation(scene.page.orientation.quarter_turns, auto_angle(scene.page))
            )
            self.message(f"Page {scene.page.index + 1}: measured tilt {scene.page.skew_deg:+.2f}°.")

    def apply_orientation_to_all(self) -> None:
        """Copy this page's orientation to every page (digital pages: quarter turns only)."""
        current = self.current_scene()
        if current is None:
            return
        o = current.page.orientation
        self._merge_session += 1
        self.undo.beginMacro("Apply orientation to all pages")
        for scene in self.scenes:
            if scene is not current:
                self._reorient(
                    scene,
                    Orientation(o.quarter_turns, o.straighten_deg if scene.page.scanned else 0.0),
                )
        self.undo.endMacro()

    def _on_page_reoriented(self, index: int) -> None:
        page = self.document.pages[index]
        self.document.spans.pop(index, None)  # OCR boxes are stale; re-read on demand
        self.page_list.item(index).setIcon(QIcon(to_pixmap(page.pixels[::4, ::4])))
        if self.current_scene() is self.scenes[index]:
            self._sync_page_controls()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.overlay.start(text)
        else:
            self.overlay.finish()
        if text:
            self.message(text)
        self._update_enabled()

    def set_tool(self, tool: Tool) -> None:
        self.tool_actions[tool].setChecked(True)
        self.view.set_tool(tool)

    # -- opening & detection ----------------------------------------------------
    # -- recent files --------------------------------------------------------------
    def recent_files(self) -> list[Path]:
        """Most recent first; files that were moved or deleted are left out."""
        stored = settings().value("recent_files", []) or []
        if isinstance(stored, str):  # QSettings returns a lone entry as a plain string
            stored = [stored]
        return [Path(p) for p in stored if Path(p).exists()]

    def add_recent(self, path: Path) -> None:
        path = path.resolve()
        files = [path, *(p for p in self.recent_files() if p.resolve() != path)][:MAX_RECENT]
        settings().setValue("recent_files", [str(p) for p in files])

    def clear_recent(self) -> None:
        settings().remove("recent_files")

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        files = self.recent_files()
        for number, path in enumerate(files, start=1):
            kind = "project" if path.name.endswith(PROJECT_SUFFIX) else path.parent.name
            action = self.recent_menu.addAction(f"&{number % 10}  {path.name}   ({kind})")
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.triggered.connect(lambda _=False, p=path: self.open_recent(p))
            action.setEnabled(not self._busy)
        if not files:
            self.recent_menu.addAction("No recent files").setEnabled(False)
        self.recent_menu.addSeparator()
        clear = self.recent_menu.addAction("Clear list", self.clear_recent)
        clear.setEnabled(bool(files))

    def open_recent(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "Open recent", f"{path} no longer exists.")
            return
        if self._confirm_discard():
            self.open_path(path)

    def close_document(self) -> bool:
        """Back to the empty start screen; asks first about unsaved work."""
        if self.document is None or self._busy or not self._confirm_discard():
            return False
        self.document, self.project_path = None, None
        self.fill_session, self.fill_scenes, self._fill_signature = None, [], None
        self.view.setScene(None)
        self.scenes = []
        self.page_list.blockSignals(True)
        self.page_list.clear()
        self.page_list.blockSignals(False)
        self.undo.clear()
        self.undo.setClean()
        self.count_label.clear()
        self.stack.setCurrentWidget(self.placeholder)
        self.set_mode(PREPARE)
        self.message("Closed.")
        return True

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open form or project", "", OPEN_FILTER)
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if path.name.endswith(PROJECT_SUFFIX) or suffix == ".json":
            self._run_job(lambda progress: load_project(path), f"Loading project {path.name}…",
                          lambda doc: self.set_document(doc, project_path=path))  # fmt: skip
        elif suffix in PDF_SUFFIXES and has_form_fields(path):
            # Already fillable: keep its fields (no duplicates) and start filling it out.
            self._run_job(lambda progress: open_fillable(path, progress=progress),
                          f"Opening {path.name}…", self._set_fillable_document)  # fmt: skip
        elif suffix in PDF_SUFFIXES | IMAGE_SUFFIXES:
            self._start_detection(path)
        else:
            QMessageBox.warning(self, "Unsupported file", f"Cannot open {path.name}.")

    def _detector(self):
        detector_id = self.detector_box.currentData()
        if detector_id not in self._detector_cache:
            if detector_id == "opencv":
                self._detector_cache[detector_id] = OpenCVDetector()
            else:
                from scan2form.core.detect.ffdnet_detector import FFDNetDetector

                self._detector_cache[detector_id] = FFDNetDetector(
                    "FFDNet-" + detector_id[-1].upper(), confidence=0.15
                )
        return self._detector_cache[detector_id]

    def ocr(self) -> Ocr:
        if self._ocr is None:
            self._ocr = Ocr()
        return self._ocr

    def _start_detection(self, path: Path) -> None:
        use_ocr = self.ocr_box.isChecked()

        def work(progress):
            return process(
                path, self._detector(), self.ocr() if use_ocr else None, progress=progress
            )

        self._run_job(work, f"Detecting fields in {path.name}…", self.set_document)

    def redetect(self) -> None:
        if self.document is None:
            return
        if (
            self.document.field_count
            and QMessageBox.question(
                self, "Detect again?", "This replaces all fields, including your edits. Continue?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._start_detection(self.document.source)

    def _run_job(self, fn, text: str, on_done) -> None:
        # Connect to bound methods of this QObject (not lambdas) so Qt queues the
        # calls onto the UI thread; widgets must never be touched from the pool.
        job = Job(fn)
        self._on_job_done = on_done
        self.progress.setRange(0, 0)
        job.signals.progress.connect(self._job_progress)
        job.signals.finished.connect(self._job_finished)
        job.signals.failed.connect(self._job_failed)
        self._set_busy(True, text)
        self.pool.start(job)

    def _job_progress(self, page: int, page_count: int, step: str) -> None:
        self.overlay.report(page, page_count, step)
        self.progress.setRange(self.overlay.bar.minimum(), self.overlay.bar.maximum())
        self.progress.setValue(self.overlay.bar.value())
        self.message(self.overlay.step.text())

    def _job_finished(self, result) -> None:
        self._set_busy(False)
        self._on_job_done(result)

    def _job_failed(self, error: str) -> None:
        self._set_busy(False, "Failed.")
        QMessageBox.critical(self, "Scan2Form", error)

    def _set_fillable_document(self, document: Document) -> None:
        self.set_document(document, mode=FILL)

    def set_document(
        self, document: Document, project_path: Path | None = None, mode: str = PREPARE
    ) -> None:
        self.document, self.project_path = document, project_path
        self.add_recent(project_path or document.source)
        self.fill_session, self.fill_scenes, self._fill_signature = None, [], None
        self.mode = PREPARE
        self.scenes = []
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for page in document.pages:
            scene = PageScene(page, self.undo, self._name_new_field)
            scene.selectionChanged.connect(self._update_panel)
            scene.message.connect(self.message)
            scene.reoriented.connect(self._on_page_reoriented)
            self.scenes.append(scene)
            item = QListWidgetItem(
                QIcon(to_pixmap(page.pixels[::4, ::4])), f"Page {page.index + 1}"
            )
            self.page_list.addItem(item)
        self.page_list.blockSignals(False)
        self.undo.clear()
        self.undo.setClean()
        self.stack.setCurrentWidget(self.view)
        self.page_list.setCurrentRow(0)
        self.show_page(0)
        self.view.fit_width()
        self.view.verticalScrollBar().setValue(0)  # start reading at the top
        self._update_title()
        self._update_enabled()
        hint = self._collect_suggestions(document)
        if mode == FILL and document.has_source_fields:
            self.message(f"{document.source.name} is already fillable: its {document.field_count} "
                         "fields were loaded. Switch to ① Prepare to change them.")  # fmt: skip
        else:
            self.message(f"{document.field_count} fields on {len(document.pages)} page(s). "
                         "Dashed orange = uncertain. Brush (B) adds missing fields." + hint)  # fmt: skip
        self.set_mode(mode)  # a restored draft is announced there (more important)

    def show_page(self, index: int) -> None:
        scenes = self.fill_scenes if self.mode == FILL else self.scenes
        if 0 <= index < len(scenes):
            zoom = self.view.zoom()
            self.view.setScene(scenes[index])
            self.view.set_zoom(zoom)
            self._merge_session = getattr(self, "_merge_session", 0) + 1
            self._update_panel()

    # -- editing --------------------------------------------------------------
    def all_names(self, exclude: Field | None = None) -> set[str]:
        if self.document is None:
            return set()
        return {
            f.name for p in self.document.pages for f in p.fields if f is not exclude and f.name
        }

    def name_is_taken(self, name: str, field: Field) -> bool:
        """Only a field of another kind blocks a name; the same kind links the fields."""
        return self.document is not None and name_conflicts(self.document, name, field)

    def linked_pages(self, field: Field) -> list[int]:
        return linked_pages(self.document, field) if self.document is not None else []

    def link_repeated_fields(self) -> None:
        """Turn "vorname_2", "vorname_3" on later pages back into "vorname": one field."""
        if self.document is None:
            return
        renames = link_repeated_names(self.document)
        if not renames:
            self.message(
                "No repeated fields to link (same label, numbered _2, _3 … on other pages)."
            )
            return
        scene_of = {id(f): self.scenes[p.index] for p in self.document.pages for f in p.fields}
        self.undo.beginMacro(f"Link {len(renames)} repeated field(s)")
        for f, name in renames:
            self.undo.push(EditProperties(scene_of[id(f)], f, name=name))
        self.undo.endMacro()
        names = sorted({name for _f, name in renames})
        self.message(f"Linked {len(renames)} field(s): {', '.join(names)} now share one value "
                     "across pages. Undo with Ctrl+Z.")  # fmt: skip
        self._update_panel()

    def _name_new_field(self, field: Field, page_index: int) -> None:
        """Name a field added by hand from the OCR labels around it (OCR runs on demand)."""
        spans = self.document.spans.get(page_index)
        if spans is None and self.ocr_box.isChecked():
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                spans = self.ocr().read(self.document.pages[page_index])
            finally:
                QApplication.restoreOverrideCursor()
            self.document.spans[page_index] = spans
        page = self.document.pages[page_index]
        on_page = {f.name for f in page.fields if f.name}
        assign_names([field], spans or [], page_index, on_page, page.pt_per_mm)
        # Same label as a text field on another page -> the same field (filled once).
        used = {
            f.name: f.type for p in self.document.pages if p is not page for f in p.fields if f.name
        }
        link_across_pages([field], used)

    def _on_property_changed(self, field: Field, attrs: dict) -> None:
        scene = self.current_scene()
        if scene is not None:
            self.undo.push(EditProperties(scene, field, **attrs))

    def arrange(self, key: str) -> None:
        scene = self.current_scene()
        if scene is None or not scene.arrange(key):
            self.message("Select at least two fields first (drag a box around them or Ctrl+click).")

    def _update_panel(self, *_args) -> None:
        scene = self.current_scene()
        selected = scene.selected_fields() if scene is not None else []
        can_arrange = len(selected) >= 2 and not self._busy
        for action in self.arrange_actions.values():
            action.setEnabled(can_arrange)
        self.arrange_button.setEnabled(can_arrange)
        if self.document is not None:
            self.count_label.setText(f"{self.document.field_count} fields")
        self.panel.show_fields(selected, scene.page.pt_per_mm if scene is not None else 72 / 25.4)
        self._sync_page_controls()

    # -- saving & export ----------------------------------------------------------
    def save_project(self) -> bool:
        if self.document is None:
            return False
        path = self.project_path
        if path is None:
            default = self.document.source.with_name(self.document.source.stem + PROJECT_SUFFIX)
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Save project", str(default), f"Scan2Form project (*{PROJECT_SUFFIX})"
            )
            if not chosen:
                return False
            path = Path(chosen)
        save_project(self.document, path)
        self.project_path = path
        self.add_recent(path)
        self.undo.setClean()
        self.message(f"Project saved: {path.name}")
        return True

    def export_dialog(self) -> None:
        if self.document is None:
            return
        default = self.document.source.with_name(self.document.source.stem + ".fillable.pdf")
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Export fillable PDF", str(default), "PDF (*.pdf)"
        )
        if chosen:
            self.export_to(Path(chosen))

    # -- autofill -------------------------------------------------------------------
    def load_vault(self) -> Vault | None:
        """The decrypted vault, or None if it is locked and the user did not reset it."""
        try:
            return self.vault_store.load()
        except VaultLockedError as exc:
            answer = QMessageBox.question(
                self, "Saved data locked",
                f"{exc}\n\nThis happens after a Windows profile reset or on another PC. "
                "Start over with empty data? The old file is deleted.",
            )  # fmt: skip
            if answer == QMessageBox.StandardButton.Yes:
                self.vault_store.reset()
                return self.vault_store.load()
            return None

    def _collect_suggestions(self, document: Document) -> str:
        """Feed labelled, unmapped fields into the vault's suggestions (only once a vault exists)."""
        if not self.vault_store.exists():
            return ""
        try:
            vault = self.vault_store.load()
        except VaultLockedError:
            return ""
        fields = [
            FormField(f.name, f.name, f.name, f.type.value)
            for p in document.pages
            for f in p.fields
            if f.name
        ]
        new = collect_suggestions(vault, fields, map_fields(fields, vault), document.source.name)
        if not new:
            return ""
        self.vault_store.save(vault)
        return f"  {new} new field suggestion(s) in Tools → My data."

    def open_my_data(self) -> None:
        vault = self.load_vault()
        if vault is not None and VaultDialog(self.vault_store, vault, self).exec():
            self.message("Your data was saved (encrypted).")
            self._reload_session_vault()

    def fill_other(self) -> None:
        if not self._confirm_discard():
            return
        chosen, _ = QFileDialog.getOpenFileName(self, "Fill out a fillable PDF", "", "PDF (*.pdf)")
        if chosen:
            self.open_path(Path(chosen))

    # -- modes ------------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Switch between ① Prepare and ② Fill out; banner, frame colour, switch, title,
        toolbars and side panel all change, so the active mode is always obvious."""
        if mode == FILL and not self._enter_fill():
            mode = PREPARE
        self.mode = mode
        filling = mode == FILL
        (self.act_mode_fill if filling else self.act_mode_prepare).setChecked(True)
        color = MODE_COLORS[mode]
        self.banner.setText(MODE_BANNERS[mode])
        self.banner.setStyleSheet(
            f"background: {color}; color: white; font-size: 14px; font-weight: 600; "
            "padding: 8px 12px;"
        )
        self.view.setStyleSheet(f"QGraphicsView {{ border: 4px solid {color}; }}")
        self.prepare_bar.setVisible(not filling)
        self.page_bar.setVisible(not filling)
        self.fill_bar.setVisible(filling)
        self.side_stack.setCurrentWidget(self.fill_panel if filling else self.panel)
        self.side_dock.setWindowTitle("Where the values come from" if filling else "Properties")
        # Ctrl+S saves what the current mode produces.
        save = QKeySequence(QKeySequence.StandardKey.Save)
        self.act_save.setShortcut(QKeySequence() if filling else save)
        self.act_save_copy.setShortcut(save if filling else QKeySequence())
        # The File menu shows only what this mode can save.
        for action in (self.act_save, self.act_export):
            action.setVisible(not filling)
        for action in (self.act_save_copy, self.act_save_copy_as):
            action.setVisible(filling)
        self._update_fill_status()
        if filling:
            self.view.setDragMode(PageView.DragMode.NoDrag)
            self.view.viewport().unsetCursor()
        else:
            self.view.set_tool(self.view.tool)
        # Same page, same place: switching modes must not jump around.
        scroll = (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())
        self.show_page(max(0, self.page_list.currentRow()))
        self.view.horizontalScrollBar().setValue(scroll[0])
        self.view.verticalScrollBar().setValue(scroll[1])
        self._update_title()
        self._update_enabled()
        self._show_restored_note()

    def _show_restored_note(self) -> None:
        note, self._restored_note = getattr(self, "_restored_note", None), None
        if note:
            self.message(note)

    def _enter_fill(self) -> bool:
        if self.document is None:
            return False
        if not self.document.source_form_unchanged:
            renames = ensure_unique_names(self.document)
            if renames:  # every field needs its own name to hold its own value
                scene_of = {
                    id(f): self.scenes[p.index] for p in self.document.pages for f in p.fields
                }
                self.undo.beginMacro("Name unnamed fields")
                for f, name in renames:
                    self.undo.push(EditProperties(scene_of[id(f)], f, name=name))
                self.undo.endMacro()
        signature = self.document.signature()
        if self.fill_session is not None and signature == self._fill_signature:
            return True
        fields = form_fields_of(self.document)
        if not fields:
            QMessageBox.information(
                self, "Fill out", "This form has no fields yet. Add some in ① Prepare first."
            )
            return False
        vault = self.load_vault()
        if vault is None:
            return False
        previous = self.fill_session
        session = FillSession(fields, vault)
        if previous is not None:
            session.carry_over(previous)
        else:
            if self.document.has_source_fields and self.document.source.suffix.lower() == ".pdf":
                session.load_existing(read_form_values(self.document.source))  # partly filled PDF
            draft = vault.drafts.get(draft_key(self.document))
            restored = session.apply_draft(draft) if draft else 0
            session.fill_from_profile()  # pre-filled from your data, today's date included
            if restored:
                self._restored_note = (f"Restored {restored} value(s) you typed earlier "
                                       "(kept as an encrypted draft).")  # fmt: skip
        self.fill_session, self._fill_signature = session, signature
        self.fill_scenes = []
        for page in self.document.pages:
            scene = FillScene(page, session)
            scene.edited.connect(self._on_fill_edited)
            scene.committed.connect(self._on_fill_committed)
            scene.focused.connect(self.fill_panel.select_field)
            self.fill_scenes.append(scene)
        self.fill_panel.set_session(session)
        self._reload_profile_box()
        return True

    def _reload_profile_box(self) -> None:
        session = self.fill_session
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for profile in session.vault.profiles:
            self.profile_box.addItem(profile.name, profile.id)
        self.profile_box.setCurrentIndex(max(0, self.profile_box.findData(session.profile_id)))
        self.profile_box.blockSignals(False)

    def _refresh_fill(self, field_id: str | None = None) -> None:
        for scene in self.fill_scenes:
            scene.refresh(field_id)
        self.fill_panel.update_summary()
        self._update_fill_status()

    def _on_fill_edited(self, _field_id: str) -> None:
        self.fill_panel.update_summary()
        self._update_fill_status()
        self._draft_timer.start()

    def _on_fill_committed(self, field_id: str) -> None:
        """Left a box: learn new data from it (stored in My data at once)."""
        session = self.fill_session
        if session is None or field_id not in session.typed:
            return
        label = session.learn_typed(field_id)
        self.fill_panel.sync_mapping(field_id)
        if label:
            # Other fields asking for the same datum (e.g. on another page) get it, too.
            if session.fill_from_profile():
                self._refresh_fill()
            self.message(f"Saved “{label}” to My data (profile “{session.profile.name}”).")
        self._flush_draft()

    def _flush_draft(self) -> None:
        """Write the typed values (encrypted) so nothing is lost before the copy is saved."""
        self._draft_timer.stop()
        session = self.fill_session
        if session is None or self.document is None:
            return
        key = draft_key(self.document)
        draft = session.to_draft()
        if draft["values"] or draft["cleared"]:
            session.vault.drafts[key] = draft
        elif key not in session.vault.drafts:
            self.vault_store.save(session.vault)  # learned mappings etc.
            return
        else:
            session.vault.drafts.pop(key)
        self.vault_store.save(session.vault)

    def _update_fill_status(self) -> None:
        """'not saved' / 'saved' next to the save button, and * in the title bar."""
        session = self.fill_session
        if self.mode != FILL or session is None:
            self.fill_status.clear()
            self.setWindowModified(self.mode == PREPARE and not self.undo.isClean())
            return
        unsaved = session.dirty and bool(session.values)
        if unsaved:
            text, color = "●  not saved yet", "#d27800"
        elif session.output is not None:
            text, color = f"✓  saved: {session.output.name}", MODE_COLORS[FILL]
        else:
            text, color = "", "black"
        self.fill_status.setText(f"  {text}  ")
        self.fill_status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.fill_status.setToolTip(str(session.output) if session.output else "")
        self.setWindowModified(unsaved)

    def _on_fill_mapping_changed(self, field_id: str) -> None:
        self._refresh_fill(field_id)
        self._flush_draft()  # the new mapping is remembered right away

    def _on_profile_changed(self, _index: int) -> None:
        if self.fill_session is not None and self.profile_box.currentData():
            self.fill_session.profile_id = self.profile_box.currentData()
            self.fill_from_profile()

    def fill_from_profile(self) -> None:
        if self.fill_session is not None:
            changed = self.fill_session.fill_from_profile()
            self._refresh_fill()
            name, kept = self.fill_session.profile.name, self.fill_session.kept
            hint = f" Kept {kept} value(s) you entered yourself." if kept else ""
            self.message(f"Filled {len(changed)} field(s) from “{name}”.{hint}")

    def clear_fill_values(self) -> None:
        if self.fill_session is None or not self.fill_session.values:
            return
        answer = QMessageBox.question(
            self, "Clear all", "Remove all filled-in values from this form?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.fill_session.clear()
            self._refresh_fill()
            self._flush_draft()

    def focus_fill_field(self, field_id: str) -> None:
        for index, scene in enumerate(self.fill_scenes):
            if field_id in scene.editors:
                if self.view.scene() is not scene:
                    self.page_list.setCurrentRow(index)
                item = scene.focus_field(field_id)
                if item is not None:
                    self.view.ensureVisible(item)
                return

    def _save_copy_clicked(self) -> None:
        self.save_filled_copy()

    def _save_copy_as_clicked(self) -> None:
        self.save_filled_copy(ask=True)

    def save_filled_copy(self, output: Path | None = None, ask: bool = False) -> Path | None:
        """Write the filled copy; again to the same file once it has been saved (unless `ask`)."""
        if self.fill_session is None or self.document is None:
            return None
        if output is None and not ask:
            output = self.fill_session.output
        if output is None:
            default = default_output(self.document.source)
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Save filled copy", str(default), "PDF (*.pdf)"
            )
            if not chosen:
                return None
            output = Path(chosen)
        pending = self.fill_session.describe_pending()
        if pending:
            answer = QMessageBox.question(
                self, "Update My data?",
                "You entered different values than stored in your data:\n\n"
                + "\n".join(pending) + "\n\nUse the new values from now on?",
            )  # fmt: skip
            if answer == QMessageBox.StandardButton.Yes:
                self.fill_session.apply_pending()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = self.fill_session.write_copy(self.document, output)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Save filled copy", str(exc))
            return None
        finally:
            QApplication.restoreOverrideCursor()
        self.fill_session.remember(self.document.source.name)
        self.fill_session.vault.drafts.pop(draft_key(self.document), None)  # saved for real now
        self.vault_store.save(self.fill_session.vault)
        self.message(f"Filled copy saved ({count} fields) → {output}")
        self._update_fill_status()
        return output

    def _reload_session_vault(self) -> None:
        """After 'My data': use the new data and fill fields that are still empty."""
        if self.fill_session is None:
            return
        vault = self.load_vault()
        if vault is None:
            return
        session = self.fill_session
        session.vault = vault
        if session.profile_id not in {p.id for p in vault.profiles}:
            session.profile_id = vault.profiles[0].id
        manual = {fid: session.mappings[fid] for fid in session.manual}
        session.mappings = map_fields(session.fields, vault) | manual
        session.fill_from_profile()
        self.fill_panel.set_session(session)
        self._reload_profile_box()
        self._refresh_fill()

    def export_to(self, path: Path) -> int:
        if path.resolve() == self.document.source.resolve():
            QMessageBox.warning(
                self, "Export", "Choose a different file; the original stays untouched."
            )
            return 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = export_pdf(self.document.source, self.document.pages, path,
                               replace_existing=self.document.has_source_fields)  # fmt: skip
        finally:
            QApplication.restoreOverrideCursor()
        self.message(f"Exported {count} fields → {path}")
        return count

    # -- window events ----------------------------------------------------------
    def _confirm_discard(self) -> bool:
        # Typed values survive as an encrypted draft and come back with this form.
        self._flush_draft()
        if self.document is None or self.undo.isClean():
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save your edits as a project first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )  # fmt: skip
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:
        if self._busy or not self._confirm_discard():
            event.ignore()
            return
        event.accept()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and not self._busy:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and self._confirm_discard():
            self.open_path(Path(urls[0].toLocalFile()))
