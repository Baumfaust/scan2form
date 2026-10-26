from dataclasses import astuple

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from scan2form.core.detect import OpenCVDetector
from scan2form.core.export import export_pdf
from scan2form.core.groundtruth import load_ground_truth
from scan2form.core.model import BBox, Field, FieldType
from scan2form.core.pipeline import load_project, process, save_project


def test_flat_pdf_becomes_fillable_with_named_fields(flat_form, tmp_path):
    document = process(flat_form, OpenCVDetector(), ocr=None)
    out = tmp_path / "out.pdf"

    count = export_pdf(flat_form, document.pages, out)

    fields = PdfReader(out).get_fields()
    assert count == len(fields) == 5
    types = sorted(f["/FT"] for f in fields.values())
    assert types == ["/Btn", "/Btn", "/Tx", "/Tx", "/Tx"]
    # Exported geometry reads back to the detected geometry.
    readback = load_ground_truth(out)[0]
    detected = document.pages[0].fields
    assert all(max(r.bbox.iou(d.bbox) for d in detected) > 0.95 for r in readback)


def test_export_on_rotated_page_keeps_view_coordinates(flat_form, tmp_path):
    rotated = tmp_path / "rotated.pdf"
    writer = PdfWriter(clone_from=flat_form)
    writer.pages[0].rotate(90)
    writer.write(rotated)
    document = process(rotated, OpenCVDetector(), ocr=None)
    marker = Field(FieldType.TEXT, BBox(100, 50, 300, 70), name="marker")
    document.pages[0].fields = [marker]
    out = tmp_path / "out.pdf"

    export_pdf(rotated, document.pages, out)

    (readback,) = load_ground_truth(out)[0]
    assert astuple(readback.bbox) == pytest.approx(astuple(marker.bbox), abs=0.05)


def test_image_input_becomes_pdf_page_of_matching_size(flat_form, tmp_path):
    from scan2form.core.ingest import load_pages

    page = next(load_pages(flat_form, dpi=150))
    image_path = tmp_path / "scan.png"
    Image.fromarray(page.pixels).save(image_path, dpi=(150, 150))
    document = process(image_path, OpenCVDetector(), ocr=None)
    out = tmp_path / "out.pdf"

    export_pdf(image_path, document.pages, out)

    reader = PdfReader(out)
    assert float(reader.pages[0].mediabox.width) == pytest.approx(595, abs=1)
    assert len(reader.get_fields()) == 5


def test_project_roundtrip(flat_form, tmp_path):
    document = process(flat_form, OpenCVDetector(), ocr=None)
    path = tmp_path / "project.json"
    save_project(document, path)
    restored = load_project(path)
    assert [(f.name, f.type, f.bbox) for f in restored.pages[0].fields] == [
        (f.name, f.type, f.bbox) for f in document.pages[0].fields
    ]


def test_same_label_on_two_pages_is_one_field(flat_form, tmp_path):
    from pypdf import PdfReader, PdfWriter

    from scan2form.core.autofill.fill import fill_pdf, read_form_fields
    from scan2form.core.detect import OpenCVDetector
    from scan2form.core.model import FieldType
    from scan2form.core.naming import slugify
    from scan2form.core.pipeline import link_repeated_names, process

    two = tmp_path / "two.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_page(PdfReader(flat_form).pages[0])
    writer.write(two)

    document = process(two, OpenCVDetector(), ocr=None)
    p1, p2 = document.pages
    # Without OCR the names are generic (per page); give both pages the same labels.
    for page in (p1, p2):
        texts = [f for f in page.fields if f.type is FieldType.TEXT and not f.multiline]
        texts[0].name = slugify("Vorname")
    p2_vorname = next(f for f in p2.fields if f.name == "vorname")
    p2_vorname.name = "vorname_2"  # as the old naming did
    assert link_repeated_names(document) == [(p2_vorname, "vorname")]
    p2_vorname.name = "vorname"

    out = tmp_path / "two.fillable.pdf"
    export_pdf(two, document.pages, out)
    fields = [f for f in read_form_fields(out) if f.id == "vorname"]
    assert len(fields) == 1  # one field, two widgets
    filled = tmp_path / "two.filled.pdf"
    fill_pdf(out, {"vorname": "Erika"}, filled)
    reader = PdfReader(filled)
    assert reader.get_fields()["vorname"]["/V"] == "Erika"
    shown = []
    for page in reader.pages:
        for annot in page["/Annots"]:
            annot = annot.get_object()
            if (
                annot.get("/Parent") is not None
                and annot["/Parent"].get_object().get("/T") == "vorname"
            ):
                shown.append(b"Erika" in annot["/AP"]["/N"].get_object().get_data())
    assert shown == [True, True]  # the value appears on both pages


def test_detection_links_text_fields_but_not_checkboxes_across_pages():
    from scan2form.core.model import BBox, Field, FieldType
    from scan2form.core.pipeline import link_across_pages

    used = {"vorname": FieldType.TEXT, "ja": FieldType.CHECKBOX}
    page2 = [Field(FieldType.TEXT, BBox(0, 0, 9, 9), name="vorname"),
             Field(FieldType.CHECKBOX, BBox(0, 0, 9, 9), name="ja")]  # fmt: skip
    link_across_pages(page2, used)
    assert [f.name for f in page2] == ["vorname", "ja_2"]
