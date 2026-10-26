from dataclasses import astuple

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QMessageBox
from reportlab.lib.pagesizes import A4

from scan2form.core.detect import OpenCVDetector
from scan2form.core.groundtruth import read_pdf_fields
from scan2form.core.model import FieldType
from scan2form.core.pipeline import process
from scan2form.ui.main_window import MainWindow
from scan2form.ui.page_view import Tool

PAGE_H = A4[1]


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Modal dialogs would block forever offscreen: record them and answer like a cautious user."""
    shown = []

    def fake(answer):
        return lambda *args, **kwargs: shown.append(args[1:3]) or answer

    monkeypatch.setattr(QMessageBox, "question", fake(QMessageBox.StandardButton.Discard))
    monkeypatch.setattr(QMessageBox, "warning", fake(QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical", fake(QMessageBox.StandardButton.Ok))
    return shown


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Recent files etc. go to a temporary ini file, never the user's registry."""
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        "scan2form.ui.main_window.settings", lambda: QSettings(path, QSettings.Format.IniFormat)
    )


@pytest.fixture
def window(qtbot, flat_form):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ocr_box.setChecked(False)  # keep UI tests fast; OCR naming is tested in core
    win.set_document(process(flat_form, OpenCVDetector(), ocr=None))
    return win


def fields(win):
    return win.document.pages[0].fields


def test_document_shows_one_item_per_field(window):
    scene = window.current_scene()
    assert len(fields(window)) == 5
    assert all(scene.item_for(f) is not None for f in fields(window))
    assert window.undo.isClean()


def test_delete_and_undo(window, qtbot):
    scene = window.current_scene()
    target = fields(window)[0]
    scene.item_for(target).setSelected(True)
    qtbot.keyClick(window.view, Qt.Key.Key_Delete)
    assert target not in fields(window)
    assert scene.item_for(target) is None
    window.undo.undo()
    assert target in fields(window)
    assert scene.item_for(target) is not None


def test_brush_stroke_on_line_adds_snapped_field(window):
    scene = window.current_scene()
    for f in list(fields(window)):  # start from an empty page
        scene.item_for(f).setSelected(True)
    scene.delete_selected()

    y = PAGE_H - 551  # just above the printed 200 pt line at x 150..350
    field = window.view.finish_stroke([QPointF(x, y + (x % 3) * 0.4) for x in range(200, 262, 4)])

    assert field in fields(window)
    assert field.type is FieldType.TEXT
    assert field.bbox.x0 == pytest.approx(150, abs=2)
    assert field.bbox.x1 == pytest.approx(350, abs=2)
    assert field.name  # always named, generic without OCR


def test_click_with_brush_finds_checkbox(window):
    scene = window.current_scene()
    for f in list(fields(window)):
        scene.item_for(f).setSelected(True)
    scene.delete_selected()

    field = window.view.finish_stroke([QPointF(206, PAGE_H - 505)])  # inside the 2nd checkbox
    assert field.type is FieldType.CHECKBOX


def test_nudge_is_one_undo_step(window, qtbot):
    scene = window.current_scene()
    target = fields(window)[0]
    before = target.bbox
    scene.item_for(target).setSelected(True)
    qtbot.keyClick(window.view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert target.bbox.x0 == pytest.approx(before.x0 + 5)
    assert not window.undo.isClean()
    window.undo.undo()
    assert target.bbox == before
    assert scene.item_for(target).live_bbox().x0 == pytest.approx(before.x0)


def test_rename_links_same_kind_and_rejects_other_kinds(window):
    scene = window.current_scene()
    first = next(f for f in fields(window) if f.type is FieldType.TEXT)
    other_text = next(f for f in fields(window) if f.type is FieldType.TEXT and f is not first)
    checkbox = next(f for f in fields(window) if f.type is FieldType.CHECKBOX)
    scene.item_for(first).setSelected(True)
    panel = window.panel

    panel.name.setText(checkbox.name)  # a checkbox cannot share a text field's value
    panel._commit_name()
    assert first.name != checkbox.name and "another type" in panel.error.text()

    panel.name.setText(other_text.name)  # same kind: becomes one linked field
    panel._commit_name()
    assert first.name == other_text.name and "Linked" in panel.info.text()
    window.undo.undo()

    panel.name.setText("vorname")
    panel._commit_name()
    assert first.name == "vorname"
    window.undo.undo()
    assert first.name != "vorname"


def test_change_type_via_panel(window):
    scene = window.current_scene()
    target = next(f for f in fields(window) if f.type is FieldType.TEXT)
    scene.item_for(target).setSelected(True)
    window.panel.type.setCurrentIndex(window.panel.type.findData(FieldType.SIGNATURE))
    window.panel._commit_type()
    assert target.type is FieldType.SIGNATURE


def test_export_writes_edited_fields(window, tmp_path):
    scene = window.current_scene()
    scene.item_for(fields(window)[0]).setSelected(True)
    scene.delete_selected()
    out = tmp_path / "out.pdf"
    assert window.export_to(out) == 4
    assert len(PdfReader(out).get_fields()) == 4


def test_open_runs_detection_in_background(qtbot, flat_form):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ocr_box.setChecked(False)
    win.open_path(flat_form)
    qtbot.waitUntil(lambda: win.document is not None, timeout=20000)
    assert win.document.field_count == 5
    assert win.act_export.isEnabled()


def test_save_and_reopen_project(window, qtbot, tmp_path):
    window.project_path = tmp_path / "p.s2f.json"
    window.current_scene().item_for(fields(window)[0]).setSelected(True)
    window.current_scene().delete_selected()
    assert window.save_project()
    assert window.undo.isClean()

    win = MainWindow()
    qtbot.addWidget(win)
    win.open_path(window.project_path)
    qtbot.waitUntil(lambda: win.document is not None, timeout=20000)
    assert win.document.field_count == 4


def test_rubber_band_selects_only_fully_enclosed_fields(window):
    # QTest cannot simulate a held-button drag, so apply the rubber band's own
    # selection the way QGraphicsView does: a selection area with the view's mode.
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath

    view, scene = window.view, window.current_scene()
    assert view.rubberBandSelectionMode() == Qt.ItemSelectionMode.ContainsItemShape
    band = QPainterPath()
    # Around both checkboxes (view y ~331..342), cutting through the "Strasse" field above (y ~274..292).
    band.addRect(QRectF(QPointF(60, 282), QPointF(230, 352)))
    scene.setSelectionArea(
        band, mode=view.rubberBandSelectionMode(), deviceTransform=view.viewportTransform()
    )

    assert sorted(f.type.value for f in scene.selected_fields()) == ["checkbox", "checkbox"]
    assert window.arrange_button.isEnabled()
    assert window.panel.arrange_box.isVisibleTo(window.panel)


def test_make_uniform_on_hand_added_column_is_one_undo_step(window):
    from scan2form.core.model import BBox

    scene = window.current_scene()
    for f in list(fields(window)):
        scene.item_for(f).setSelected(True)
    scene.delete_selected()
    sloppy = [BBox(100, 400, 290, 416), BBox(103, 430, 310, 449), BBox(98, 462, 300, 478)]
    for box in sloppy:
        scene.add_from_mark(box, exact=True)
    scene.clearSelection()
    for f in fields(window):
        scene.item_for(f).setSelected(True)
    steps_before = window.undo.index()

    window.arrange("uniform")

    boxes = [f.bbox for f in fields(window)]
    assert len({(b.x0, b.x1, round(b.height, 6)) for b in boxes}) == 1
    assert [b.y1 for b in boxes] == [416, 449, 478]  # rows stay on their lines
    assert window.undo.index() == steps_before + 1
    window.undo.undo()
    assert [f.bbox for f in fields(window)] == sloppy


def test_arrange_needs_two_fields(window):
    window.current_scene().clearSelection()
    window.arrange("uniform")
    assert "at least two" in window.status_text.text()
    assert not window.arrange_button.isEnabled()


def test_app_icon_has_all_sizes(qtbot):
    from scan2form.ui.app import ICON_SIZES, app_icon

    icon = app_icon()
    assert not icon.isNull()
    assert {s.width() for s in icon.availableSizes()} == set(ICON_SIZES)


def test_progress_overlay_shows_determinate_progress(qtbot, flat_form):
    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    win.ocr_box.setChecked(False)
    seen = []
    win.overlay.bar.valueChanged.connect(lambda v: seen.append((v, win.overlay.bar.maximum())))

    win.open_path(flat_form)
    assert win.overlay.isVisible()
    assert win.overlay.card.width() >= 460
    qtbot.waitUntil(lambda: win.document is not None, timeout=20000)

    assert not win.overlay.isVisible()
    assert seen and all(maximum == 3 for _, maximum in seen if maximum)  # 1 page x 3 steps
    assert "Page 1 of 1" in win.overlay.step.text()


def _filled_vault():
    from scan2form.core.autofill.vault import Profile, Vault

    return Vault(
        [
            Profile(
                "Ich", {"first_name": "Erika", "street": "Heidestraße", "marital_status": "ledig"}
            )
        ]
    )


def test_vault_dialog_edits_profiles_and_saves_encrypted(qtbot, tmp_path):
    from scan2form.core.autofill.vault import VaultStore
    from scan2form.ui.vault_dialog import VaultDialog

    store = VaultStore(tmp_path / "v.s2fv")
    dialog = VaultDialog(store, _filled_vault())
    qtbot.addWidget(dialog)
    row = next(
        r
        for r in range(dialog.table.rowCount())
        if dialog.table.item(r, 1).data(0x0100) == "birth_date"
    )
    dialog.table.item(row, 2).setText("12.08.1964")
    dialog._add_custom("Kundennummer")
    dialog.accept()

    saved = store.load()
    assert saved.profiles[0].values["birth_date"] == "1964-08-12"  # stored ISO
    assert saved.profiles[0].values["first_name"] == "Erika"
    assert [c.label for c in saved.custom_keys] == ["Kundennummer"]


def _prepare_named(window):
    texts = [f for f in fields(window) if f.type is FieldType.TEXT and not f.multiline]
    texts[0].name, texts[1].name = "vorname", "kundennummer"  # as OCR would name them
    next(f for f in fields(window) if f.type is FieldType.CHECKBOX).name = "ledig"
    window.vault_store.save(_filled_vault())


def test_mode_switch_changes_everything_visible(window):
    _prepare_named(window)
    assert "PREPARE" in window.banner.text() and "Prepare form" in window.windowTitle()
    assert window.prepare_bar.isVisibleTo(window) and not window.fill_bar.isVisibleTo(window)

    window.act_mode_fill.trigger()
    assert window.mode == "fill" and window.act_mode_fill.isChecked()
    assert "FILL OUT" in window.banner.text() and "Fill out" in window.windowTitle()
    assert window.fill_bar.isVisibleTo(window) and not window.prepare_bar.isVisibleTo(window)
    assert not window.page_bar.isVisibleTo(window)
    assert window.side_stack.currentWidget() is window.fill_panel
    assert not window.act_detect.isEnabled() and not window.tool_actions[Tool.BRUSH].isEnabled()
    assert window.current_scene() is None  # fields cannot be moved or deleted while filling

    window.act_mode_prepare.trigger()
    assert window.mode == "prepare" and window.act_detect.isEnabled()
    assert window.current_scene() is window.scenes[0]


def test_fill_on_the_page_learns_keeps_draft_and_saves_copy(window, qtbot, tmp_path, monkeypatch):
    from scan2form.core.autofill.mapping import map_fields
    from scan2form.core.autofill.session import draft_key

    _prepare_named(window)
    window.set_mode("fill")
    scene = window.view.scene()
    assert scene.editors["vorname"][0].text() == "Erika"  # pre-filled from the profile
    assert scene.editors["ledig"][0].checked  # Familienstand = ledig
    customer = scene.editors["kundennummer"][0]
    qtbot.keyClicks(customer, "4711")
    customer.editingFinished.emit()  # leaving the box

    # New data is learned at once and the typed value is kept as an encrypted draft.
    vault = window.vault_store.load()
    mapping = map_fields(window.fill_session.fields, vault)["kundennummer"]
    assert vault.profiles[0].values[mapping.key] == "4711"
    assert vault.drafts[draft_key(window.document)]["values"] == {"kundennummer": "4711"}

    # Changing stored data is asked before saving the copy.
    vorname = scene.editors["vorname"][0]
    vorname.selectAll()
    qtbot.keyClicks(vorname, "Erika Maria")
    vorname.editingFinished.emit()
    assert window.vault_store.load().profiles[0].values["first_name"] == "Erika"  # not yet
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)  # fmt: skip
    out = window.save_filled_copy(tmp_path / "flat.filled.pdf")
    assert asked and "Erika Maria" in asked[0]
    filled = PdfReader(out).get_fields()
    assert filled["vorname"]["/V"] == "Erika Maria" and filled["kundennummer"]["/V"] == "4711"
    assert filled["ledig"]["/V"] == "/Yes"
    vault = window.vault_store.load()
    assert vault.profiles[0].values["first_name"] == "Erika Maria"
    assert draft_key(window.document) not in vault.drafts  # saved for real: draft gone
    # The project never contains filled-in values.
    window.project_path = tmp_path / "p.s2f.json"
    window.save_project()
    assert "4711" not in window.project_path.read_text(encoding="utf-8")


def test_typed_values_come_back_after_closing_without_saving(window, qtbot, flat_form):
    _prepare_named(window)
    window.set_mode("fill")
    customer = window.view.scene().editors["kundennummer"][0]
    qtbot.keyClicks(customer, "0815")  # typed, not even left the box
    assert window.close_document()  # no question needed: nothing is lost

    window.set_document(process(flat_form, OpenCVDetector(), ocr=None))
    texts = [f for f in fields(window) if f.type is FieldType.TEXT and not f.multiline]
    texts[0].name, texts[1].name = "vorname", "kundennummer"  # same labels as before
    window.set_mode("fill")
    assert window.view.scene().editors["kundennummer"][0].text() == "0815"
    assert "Restored 1 value" in window.status_text.text()


def test_values_survive_editing_fields_in_prepare(window, qtbot):
    _prepare_named(window)
    window.set_mode("fill")
    qtbot.keyClicks(window.view.scene().editors["kundennummer"][0], "4711")
    window.set_mode("prepare")
    item = window.scenes[0].item_for(next(f for f in fields(window) if f.name == "vorname"))
    item.setSelected(True)
    window.scenes[0].nudge(5, 0)
    window.set_mode("fill")
    assert window.view.scene().editors["kundennummer"][0].text() == "4711"


def test_opening_fillable_pdf_starts_in_fill_mode_without_duplicates(window, qtbot, tmp_path):
    template = tmp_path / "form.fillable.pdf"
    count = window.export_to(template)

    win = MainWindow()
    qtbot.addWidget(win)
    win.open_path(template)
    qtbot.waitUntil(lambda: win.document is not None, timeout=20000)
    assert win.mode == "fill" and win.document.field_count == count
    assert win.document.source_form_unchanged

    win.set_mode("prepare")
    win.scenes[0].item_for(fields(win)[0]).setSelected(True)
    win.scenes[0].nudge(3, 0)
    out = tmp_path / "again.fillable.pdf"
    assert win.export_to(out) == count
    assert sum(map(len, read_pdf_fields(out).values())) == count  # replaced, not duplicated


@pytest.fixture
def crooked_two_pages(flat_form, tmp_path):
    """Two-page image-only scan; page 1 tilted by 2 deg, page 2 by -1 deg."""
    import cv2
    from PIL import Image

    from scan2form.core.ingest import load_pages

    page = next(load_pages(flat_form, dpi=150))
    h, w = page.pixels.shape[:2]
    frames = [
        Image.fromarray(
            cv2.warpAffine(
                page.pixels,
                cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0),
                (w, h),
                borderValue=(255, 255, 255),
            )
        )
        for angle in (2.0, -1.0)
    ]
    path = tmp_path / "scan2.pdf"
    frames[0].save(path, "PDF", resolution=150, save_all=True, append_images=frames[1:])
    return path


@pytest.fixture
def scan_window(qtbot, crooked_two_pages):
    win = MainWindow()
    qtbot.addWidget(win)
    win.ocr_box.setChecked(False)
    win.set_document(process(crooked_two_pages, OpenCVDetector(), ocr=None))
    return win


def test_scanned_page_is_straightened_and_controls_show_angle(scan_window):
    assert scan_window.fine_slider.isEnabled()
    assert scan_window.fine_spin.value() == pytest.approx(-2.0, abs=0.3)


def test_digital_page_has_no_fine_straightening(window):
    assert not window.fine_slider.isEnabled()
    assert window.act_rotate_right.isEnabled()


def test_rotate_right_swaps_page_and_moves_fields_undoably(scan_window):
    scene = scan_window.current_scene()
    page = scene.page
    size, boxes = (page.width_pt, page.height_pt), [f.bbox for f in page.fields]

    scan_window.rotate_page(1)

    assert (page.width_pt, page.height_pt) == size[::-1]
    assert scene.sceneRect().width() == pytest.approx(page.width_pt)
    assert all(scene.item_for(f).live_bbox() == f.bbox for f in page.fields)
    assert [f.bbox for f in page.fields] != boxes
    scan_window.undo.undo()
    assert (page.width_pt, page.height_pt) == size
    flat = [v for f in page.fields for v in astuple(f.bbox)]
    assert flat == pytest.approx([v for b in boxes for v in astuple(b)])


def test_one_slider_drag_is_one_undo_step(scan_window):
    start = scan_window.undo.index()
    scan_window._fine_drag_started()
    for degrees in (-1.5, -1.0, -0.4):
        scan_window.set_fine_angle(degrees)
    assert scan_window.undo.index() == start + 1
    assert scan_window.current_scene().page.orientation.straighten_deg == pytest.approx(-0.4)
    scan_window._fine_drag_started()  # a second drag is a separate step
    scan_window.set_fine_angle(0.5)
    assert scan_window.undo.index() == start + 2
    scan_window.undo.undo()
    scan_window.undo.undo()
    assert scan_window.current_scene().page.orientation.straighten_deg == pytest.approx(
        -2.0, abs=0.3
    )


def test_apply_to_all_pages(scan_window):
    scan_window.rotate_page(1)
    scan_window.apply_orientation_to_all()
    second = scan_window.document.pages[1]
    assert second.orientation == scan_window.document.pages[0].orientation
    scan_window.undo.undo()  # one macro step
    assert second.orientation.quarter_turns == 0
    assert second.orientation.straighten_deg == pytest.approx(1.0, abs=0.3)  # its own auto angle


def test_export_after_rotation(scan_window, tmp_path):
    scan_window.rotate_page(1)
    out = tmp_path / "out.pdf"
    scan_window.export_to(out)
    first = PdfReader(out).pages[0]
    page = scan_window.document.pages[0]
    assert float(first.mediabox.width) == pytest.approx(page.width_pt, abs=1)
    assert float(first.mediabox.height) == pytest.approx(page.height_pt, abs=1)


def test_recent_files_menu(window, qtbot, tmp_path, flat_form):
    assert window.recent_files() == [flat_form.resolve()]  # recorded when the document opened
    window.project_path = tmp_path / "p.s2f.json"
    window.save_project()
    assert window.recent_files()[0] == (tmp_path / "p.s2f.json").resolve()

    gone = tmp_path / "gone.pdf"
    gone.write_bytes(b"x")
    window.add_recent(gone)
    gone.unlink()
    assert gone.resolve() not in window.recent_files()  # missing files are skipped

    window._fill_recent_menu()
    labels = [a.text() for a in window.recent_menu.actions() if a.text()]
    assert labels[0].startswith("&1") and "p.s2f.json" in labels[0] and labels[-1] == "Clear list"

    window.recent_menu.actions()[1].trigger()  # the form: opens it again
    qtbot.waitUntil(lambda: not window._busy, timeout=20000)
    assert window.document.source == flat_form and window.recent_files()[0] == flat_form.resolve()

    window.clear_recent()
    assert window.recent_files() == []


def test_close_returns_to_start_screen(window, no_modal_dialogs):
    window.act_close.trigger()
    assert window.document is None and window.stack.currentWidget() is window.placeholder
    assert not window.act_close.isEnabled() and not window.act_mode_fill.isEnabled()
    assert "no document" in window.windowTitle()


def test_close_asks_before_losing_edits(window, no_modal_dialogs, monkeypatch):
    window.current_scene().items()[0].setSelected(True)
    window._delete_selected()
    assert not window.undo.isClean()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not window.close_document()
    assert window.document is not None


def test_save_button_in_fill_mode_writes_copy_then_resaves(window, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    _prepare_named(window)
    window.set_mode("fill")
    target = tmp_path / "mine.filled.pdf"
    asked = []
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: asked.append(1) or (str(target), "")
    )
    # File menu shows what Fill mode saves, not the greyed-out Prepare items.
    assert window.act_save_copy.isVisible() and window.act_save_copy.isEnabled()
    assert not window.act_save.isVisible() and not window.act_export.isVisible()
    assert "not saved" in window.fill_status.text() and window.isWindowModified()

    window.act_save_copy.trigger()  # the real button, as clicked
    assert target.exists() and len(asked) == 1
    assert "saved: mine.filled.pdf" in window.fill_status.text() and not window.isWindowModified()

    qtbot.keyClicks(window.view.scene().editors["kundennummer"][0], "42")
    assert "not saved" in window.fill_status.text()
    window.act_save_copy.trigger()  # Ctrl+S again: same file, no dialog
    assert len(asked) == 1 and PdfReader(target).get_fields()["kundennummer"]["/V"] == "42"
    window.act_save_copy_as.trigger()  # "as…" always asks
    assert len(asked) == 2

    window.set_mode("prepare")
    assert window.act_save.isVisible() and not window.act_save_copy.isVisible()
