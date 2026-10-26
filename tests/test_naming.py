from scan2form.core.model import BBox, Field, FieldType
from scan2form.core.naming import assign_names, group_labels, slugify
from scan2form.core.ocr import TextSpan

MM = 72 / 25.4


def span(text, x0, y0, x1, y1):
    return TextSpan(text, BBox(x0, y0, x1, y1), 1.0)


def text_field(x0, y0, x1, y1, t=FieldType.TEXT):
    return Field(t, BBox(x0, y0, x1, y1))


def test_slugify_transliterates_german_and_strips_punctuation():
    assert slugify("Straße / Hausnr.:") == "strasse_hausnr"
    assert slugify("Größe (cm)") == "groesse_cm"
    assert slugify("Café") == "cafe"
    assert len(slugify("sehr " * 30)) <= 40


def test_words_on_a_row_are_joined():
    labels = group_labels(
        [span("Geburtsdatum", 0, 0, 60, 10), span("(TT.MM.JJJJ):", 64, 0, 120, 10)]
    )
    assert [label.text for label in labels] == ["Geburtsdatum (TT.MM.JJJJ):"]


def test_left_label_beats_label_above():
    fields = [text_field(100, 100, 300, 118)]
    spans = [span("Name:", 50, 104, 95, 116), span("Antragsteller", 100, 80, 170, 92)]
    assign_names(fields, spans, 0, set())
    assert fields[0].name == "name"


def test_label_below_line_and_signature_detection():
    fields = [text_field(300, 700, 500, 718)]
    spans = [span("(Unterschrift)", 330, 722, 420, 732)]
    assign_names(fields, spans, 0, set())
    assert fields[0].name == "unterschrift"
    assert fields[0].type is FieldType.SIGNATURE


def test_checkbox_takes_label_on_the_right():
    fields = [text_field(100, 100, 111, 111, FieldType.CHECKBOX)]
    spans = [span("Nein", 40, 100, 70, 111), span("Ja", 116, 100, 130, 111)]
    assign_names(fields, spans, 0, set())
    assert fields[0].name == "ja"


def test_names_are_unique_and_fallback_is_generic():
    fields = [
        text_field(100, 100, 300, 118),
        text_field(100, 200, 300, 218),
        text_field(100, 400, 300, 418),
    ]
    spans = [span("Telefon:", 40, 104, 95, 116), span("Telefon:", 40, 204, 95, 216)]
    taken = {"telefon_3"}
    assign_names(fields, spans, 1, taken)
    assert [f.name for f in fields] == ["telefon", "telefon_2", "text_p2_3"]
    assert {"telefon", "telefon_2", "text_p2_3"} <= taken


def test_generic_names_take_the_next_free_number():
    taken = {"text_p1_1", "text_p1_2"}
    field = text_field(100, 100, 300, 118)
    assign_names([field], [], 0, taken)
    assert field.name == "text_p1_3"


def test_checkbox_label_read_together_with_the_square():
    # Measured on the demo form: OCR's "ledig" starts left of the square (it includes it),
    # while the row label "Familienstand:" is further left.
    box = text_field(190, 337, 202, 349, FieldType.CHECKBOX)
    spans = [span("Familienstand:", 70, 335, 145, 351), span("ledig", 183, 331, 234, 355)]
    assign_names([box], spans, 0, set())
    assert box.name == "ledig"


def test_caption_under_a_line_touching_it():
    line = text_field(72, 324, 253, 343)
    assign_names([line], [span("Ort, Datum", 69, 341, 129, 360)], 1, set())
    assert line.name == "ort_datum"
