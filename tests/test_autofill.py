import base64
from datetime import date
from pathlib import Path

import keyring
import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from scan2form.core.autofill.fill import default_output, fill_pdf, read_form_fields
from scan2form.core.autofill.keys import catalogue_match, is_generic, normalize_label, resolve
from scan2form.core.autofill.mapping import (
    FormField,
    Mapping,
    accept_suggestion,
    collect_suggestions,
    compute_values,
    dismiss_suggestion,
    learn,
    map_fields,
)
from scan2form.core.autofill.vault import (
    KEYRING_SERVICE,
    KEYRING_USER,
    Profile,
    Vault,
    VaultLockedError,
    VaultStore,
)

TODAY = date(2026, 10, 26)
ME = Profile("Ich", {
    "first_name": "Erika", "last_name": "Mustermann", "street": "Heidestraße", "house_number": "17",
    "postcode": "51147", "city": "Köln", "birth_date": "1964-08-12", "marital_status": "verheiratet",
})  # fmt: skip


def text(name, label=None):
    return FormField(name, name, label or name, "text")


# -- keys -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Vorname:", "first_name"),
        ("Straße und Hausnummer", "street_and_number"),
        ("Geburtsdatum (TT.MM.JJJJ)", "birth_date"),
        ("Name des Antragstellers", "full_name"),
        ("Name, Vorname", "last_first"),
        ("PLZ / Ort", "postcode_city"),
        ("Ort, Datum", "place_date"),
        ("vorname_2", "first_name"),  # deduplicated Scan2Form name
        ("Verheiratet", "marital_status=verheiratet"),
        ("Aktenzeichen", None),
        ("Datum der Unterschrift", "today"),
        ("Unterschriftsdatum", "today"),
        ("Tel. privat", "phone"),
        ("Geb. Datum", "birth_date"),
    ],
)
def test_catalogue_matching(label, key):
    assert catalogue_match(label) == key


def test_generic_names_are_recognised():
    assert all(is_generic(n) for n in ("text_p2_7", "Text1", "Check Box3", "Kontrollkästchen12"))
    assert not is_generic("vorname")


def test_derived_values_and_dates():
    v = ME.values
    assert resolve("full_name", v) == "Erika Mustermann"
    assert resolve("last_first", v) == "Mustermann, Erika"
    assert resolve("street_and_number", v) == "Heidestraße 17"
    assert resolve("postcode_city", v) == "51147 Köln"
    assert resolve("birth_date", v) == "12.08.1964"
    assert resolve("place_date", v, today=TODAY) == "Köln, 26.10.2026"


# -- vault ------------------------------------------------------------------------
def test_vault_roundtrip_is_encrypted(tmp_path):
    store = VaultStore(tmp_path / "v.s2fv")
    vault = Vault([ME, Profile("Kind", {"first_name": "Max"})])
    store.save(vault)

    blob = store.path.read_bytes()
    assert blob.startswith(b"S2FV1")
    assert b"Mustermann" not in blob and "Köln".encode() not in blob
    loaded = store.load()
    assert [p.name for p in loaded.profiles] == ["Ich", "Kind"]
    assert loaded.profiles[0].values == ME.values


def test_missing_or_wrong_key_locks_the_vault(tmp_path, isolated_vault):
    store = VaultStore(tmp_path / "v.s2fv")
    store.save(Vault([ME]))
    keyring.set_password(
        KEYRING_SERVICE, KEYRING_USER, base64.b64encode(b"x" * 32).decode()
    )  # other key
    with pytest.raises(VaultLockedError):
        store.load()
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, "not base64!")  # corrupt entry
    with pytest.raises(VaultLockedError):
        store.load()
    keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    with pytest.raises(VaultLockedError):
        store.load()
    store.reset()
    assert not store.exists()
    assert store.load().profiles[0].values == {}  # fresh start after reset


def test_save_keeps_one_backup(tmp_path):
    store = VaultStore(tmp_path / "v.s2fv")
    store.save(Vault([ME]))
    store.save(Vault([Profile("Neu")]))
    assert store.path.with_suffix(".bak").exists()
    assert store.load().profiles[0].name == "Neu"


# -- mapping ----------------------------------------------------------------------
def test_map_and_compute_values():
    fields = [
        text("vorname"), text("nachname"), text("strasse_hausnummer"), text("plz_ort"),
        text("geburtsdatum"), FormField("verheiratet", "verheiratet", "verheiratet", "checkbox"),
        FormField("ledig", "ledig", "ledig", "checkbox"), text("aktenzeichen"), text("text_p1_3"),
    ]  # fmt: skip
    mappings = map_fields(fields, Vault([ME]))
    values = compute_values(fields, mappings, ME, TODAY)
    assert values == {
        "vorname": "Erika", "nachname": "Mustermann", "strasse_hausnummer": "Heidestraße 17",
        "plz_ort": "51147 Köln", "geburtsdatum": "12.08.1964", "verheiratet": True, "ledig": False,
    }  # fmt: skip
    assert mappings["aktenzeichen"] is None and mappings["text_p1_3"] is None


def test_tooltip_names_cryptic_third_party_fields():
    field = FormField("form1.Text7", "Text7", "Vorname:", "text")
    assert map_fields([field], Vault())[field.id].key == "first_name"


def test_learned_mapping_wins_and_applies_to_new_forms():
    vault = Vault([ME])
    rufname = text("rufname_im_alltag")
    assert map_fields([rufname], vault)[rufname.id] is None
    learn(vault, rufname, Mapping("first_name", source="manual"))
    other_form_field = text("rufname_im_alltag_2")
    assert map_fields([other_form_field], vault)[other_form_field.id].key == "first_name"


def test_suggestions_count_forms_and_can_be_accepted_or_dismissed():
    vault = Vault([ME])
    fields = [text("aktenzeichen", "Aktenzeichen"), text("text_p1_3"), text("vorname")]
    assert collect_suggestions(vault, fields, map_fields(fields, vault), "a.pdf") == 1
    assert collect_suggestions(vault, fields, map_fields(fields, vault), "a.pdf") == 0
    collect_suggestions(vault, fields, map_fields(fields, vault), "b.pdf")
    assert [(s.slug, s.count) for s in vault.suggestions] == [("aktenzeichen", 2)]

    custom = accept_suggestion(vault, "aktenzeichen")
    assert custom.label == "Aktenzeichen" and not vault.suggestions
    assert map_fields(fields, vault)["aktenzeichen"].key == custom.key

    other = [text("kundennummer")]
    collect_suggestions(vault, other, map_fields(other, vault), "c.pdf")
    dismiss_suggestion(vault, "kundennummer")
    assert collect_suggestions(vault, other, map_fields(other, vault), "d.pdf") == 0


# -- fill ---------------------------------------------------------------------------
@pytest.fixture
def third_party_form(tmp_path):
    path = tmp_path / "bank.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    form = c.acroForm
    form.textfield(name="Text1", tooltip="Vorname", x=100, y=700, width=200, height=20)
    form.textfield(
        name="Text2", tooltip="Straße und Hausnummer", x=100, y=660, width=200, height=20
    )
    form.checkbox(name="Check1", tooltip="verheiratet", x=100, y=620, size=12)
    for i, value in enumerate(("ledig", "verheiratet", "geschieden")):
        form.radio(
            name="Familienstand",
            tooltip="Familienstand",
            value=value,
            x=100 + 30 * i,
            y=580,
            size=12,
        )
    form.choice(name="Land", tooltip="Land", options=["Deutschland", "Österreich"], value="Österreich",
                x=100, y=540, width=200, height=20)  # fmt: skip
    form.textfield(name="Text3", tooltip="Aktenzeichen", x=100, y=500, width=200, height=20)
    c.showPage()
    c.save()
    return path


def test_read_third_party_fields(third_party_form):
    fields = {f.id: f for f in read_form_fields(third_party_form)}
    assert fields["Text1"].label == "Vorname"
    assert fields["Check1"].kind == "checkbox"
    assert fields["Familienstand"].kind == "radio"
    assert set(fields["Familienstand"].choices) == {"ledig", "verheiratet", "geschieden"}
    assert fields["Land"].kind == "choice" and "Deutschland" in fields["Land"].choices


def test_fill_third_party_pdf_into_a_copy(third_party_form, tmp_path):
    profile = Profile("Ich", {**ME.values, "country": "Deutschland"})
    fields = read_form_fields(third_party_form)
    values = compute_values(fields, map_fields(fields, Vault([profile])), profile, TODAY)
    out = tmp_path / "bank.filled.pdf"

    assert fill_pdf(third_party_form, values, out) == 5  # all but "Aktenzeichen"

    filled = PdfReader(out).get_fields()
    assert filled["Text1"]["/V"] == "Erika"
    assert filled["Text2"]["/V"] == "Heidestraße 17"
    assert filled["Check1"]["/V"] != "/Off"
    assert filled["Familienstand"]["/V"] == "/verheiratet"
    assert filled["Land"]["/V"] == "Deutschland"
    assert "/V" not in filled["Text3"] or not filled["Text3"]["/V"]
    assert PdfReader(third_party_form).get_fields()["Text1"].get("/V") in (
        None,
        "",
    )  # source untouched


def test_fill_refuses_to_overwrite_source(third_party_form):
    with pytest.raises(ValueError):
        fill_pdf(third_party_form, {}, third_party_form)


def test_default_output_name(tmp_path):
    assert default_output(tmp_path / "Formular.fillable.pdf").name == "Formular.filled.pdf"


def test_normalize_label_drops_filler():
    assert normalize_label("Ihre E-Mail-Adresse (optional)") == "e_mail_adresse"


# -- import from a text file ----------------------------------------------------------
PROFILE_MD = """# Test profile

Prose with an arrow -> but no colon is ignored.

## Personal Information
- **First Name (`Vorname`):** Erika
- **Last Name (`Name` / `Nachname`):** Mustermann
- **Date of Birth (`Geb. Datum` / `Geburtsdatum`):** 12.08.1964
- **Driver's License (`Führerschein`):** Klasse B
- **Health Insurance (`Krankenkasse`):** AOK

## Contact & Address
- **Street & House Number (`Straße`):** Heidestraße 17a
- **Postal Code (`Postleitzahl` / `PLZ`):** 51147
- **Phone Private (`Tel. privat`):** 0221 123456
- **Occupation / Employer (`Beruf/Arbeitgeber`):** Lehrerin / Stadt Köln
- **IBAN / Bank Account (`Konto` / `IBAN`):** DE02 1203 0000 0000 2020 51

| Feld | Wert |
|---|---|
| Wohnort | Köln |

- **Signature Place & Date (`Ort, Datum`):** Köln, 01.01.2020
"""


def test_import_profile_file():
    from scan2form.core.autofill.importer import import_entries, parse_entries

    vault = Vault()
    me = vault.profiles[0]
    result = import_entries(vault, me, parse_entries(PROFILE_MD))
    v = me.values
    assert (v["first_name"], v["last_name"], v["birth_date"]) == (
        "Erika",
        "Mustermann",
        "1964-08-12",
    )
    assert (v["street"], v["house_number"], v["postcode"], v["city"]) == (
        "Heidestraße",
        "17a",
        "51147",
        "Köln",
    )
    assert v["phone"] == "0221 123456" and v["health_insurance"] == "AOK"
    assert v["iban"].startswith("DE02")
    assert sorted(result.created) == ["Beruf/Arbeitgeber", "Führerschein"]
    assert result.skipped == [("Ort, Datum", "always filled with today's date")]
    assert "place_date" not in v and "today" not in v

    # The custom field fills a form using the same label; the signature date is today.
    fields = [text("beruf_arbeitgeber"), text("ort_datum"), text("datum_unterschrift")]
    values = compute_values(fields, map_fields(fields, vault), me, TODAY)
    assert values == {"beruf_arbeitgeber": "Lehrerin / Stadt Köln", "ort_datum": "Köln, 26.10.2026",
                      "datum_unterschrift": "26.10.2026"}  # fmt: skip

    # Importing again updates values without duplicating custom fields.
    again = import_entries(vault, me, parse_entries(PROFILE_MD.replace("AOK", "TK")))
    assert not again.created and len(vault.custom_keys) == 2 and v["health_insurance"] == "TK"


def test_import_splits_combined_values_and_keeps_explicit_parts():
    from scan2form.core.autofill.importer import import_entries, parse_entries

    vault = Vault()
    me = vault.profiles[0]
    import_entries(
        vault,
        me,
        parse_entries("Hausnummer: 5\nStraße und Hausnummer: Am Markt 7\nPLZ / Ort: 10115 Berlin"),
    )
    assert me.values == {
        "house_number": "5",
        "street": "Am Markt",
        "postcode": "10115",
        "city": "Berlin",
    }


def test_filled_text_has_one_font_size(flat_form, tmp_path):
    import re

    from scan2form.core.export import export_pdf
    from scan2form.core.ingest import load_pages
    from scan2form.core.model import BBox, Field, FieldType

    page = next(load_pages(flat_form, dpi=50))
    page.fields = [
        Field(FieldType.TEXT, BBox(50, 100 + 40 * i, 300, 100 + 40 * i + h), name=f"f{i}")
        for i, h in enumerate((16, 22, 30))
    ] + [Field(FieldType.TEXT, BBox(50, 300, 120, 316), name="narrow")]  # fmt: skip
    template = tmp_path / "t.fillable.pdf"
    export_pdf(flat_form, [page], template)
    out = tmp_path / "t.filled.pdf"
    fill_pdf(
        template,
        {"f0": "Köln", "f1": "Köln", "f2": "Köln", "narrow": "Ein sehr langer Ortsname"},
        out,
    )

    sizes = {}
    for annot in PdfReader(out).pages[0]["/Annots"]:
        annot = annot.get_object()
        data = annot["/AP"]["/N"].get_object().get_data()
        sizes[str(annot["/T"])] = float(re.search(rb"([\d.]+) Tf", data)[1])
    assert sizes["f0"] == sizes["f1"] == sizes["f2"] == 10
    assert sizes["narrow"] < 10  # shrunk to fit instead of clipped


# -- fill session (Fill mode) -----------------------------------------------------------
def test_third_party_form_opens_with_its_fields_and_fills_unchanged(third_party_form, tmp_path):
    from scan2form.core.autofill.session import FillSession, form_fields_of
    from scan2form.core.pipeline import has_form_fields, open_fillable

    assert has_form_fields(third_party_form)
    document = open_fillable(third_party_form)
    names = [f.name for f in document.pages[0].fields]
    assert names.count("Familienstand") == 3  # one widget per radio button, shared group name
    assert document.source_form_unchanged

    session = FillSession(
        form_fields_of(document), Vault([Profile("Ich", {**ME.values, "country": "Deutschland"})])
    )
    by_id = session.by_id
    assert by_id["Text1"].label == "Vorname" and by_id["Familienstand"].kind == "radio"
    assert by_id["Land"].kind == "choice"
    session.fill_from_profile(TODAY)
    assert session.value("Text1") == "Erika" and session.value("Familienstand") == "verheiratet"
    session.set_value("Text3", "AZ-42")
    out = session_out = tmp_path / "bank.filled.pdf"
    session.write_copy(document, session_out)
    filled = PdfReader(out).get_fields()
    assert filled["Text1"]["/V"] == "Erika" and filled["Text3"]["/V"] == "AZ-42"
    assert filled["Familienstand"]["/V"] == "/verheiratet" and filled["Land"]["/V"] == "Deutschland"


def test_edited_fillable_exports_radio_group_and_replaces_old_fields(third_party_form, tmp_path):
    from scan2form.core.autofill.session import FillSession, form_fields_of
    from scan2form.core.export import export_pdf
    from scan2form.core.groundtruth import read_pdf_fields
    from scan2form.core.pipeline import open_fillable

    document = open_fillable(third_party_form)
    document.pages[0].fields.pop()  # drop "Aktenzeichen": the form is no longer the original
    assert not document.source_form_unchanged
    out = tmp_path / "edited.fillable.pdf"
    export_pdf(third_party_form, document.pages, out, replace_existing=True)
    fields = {f.id: f for f in read_form_fields(out)}
    assert "Text3" not in fields and len(read_pdf_fields(out)[0]) == len(document.pages[0].fields)
    assert fields["Familienstand"].kind == "radio"
    assert set(fields["Familienstand"].choices) == {"ledig", "verheiratet", "geschieden"}

    session = FillSession(form_fields_of(document), Vault([ME]))
    session.fill_from_profile(TODAY)
    copy = tmp_path / "edited.filled.pdf"
    session.write_copy(document, copy)
    assert PdfReader(copy).get_fields()["Familienstand"]["/V"] == "/verheiratet"


def test_ensure_unique_names_keeps_linked_fields_and_radio_groups():
    import numpy as np

    from scan2form.core.model import BBox, Field, FieldType, PageImage
    from scan2form.core.pipeline import Document, ensure_unique_names

    box = BBox(0, 0, 10, 10)
    fields = [Field(FieldType.TEXT, box, name="ort"), Field(FieldType.TEXT, box, name="ort"),
              Field(FieldType.TEXT, box), Field(FieldType.CHECKBOX, box, name="fs", choice="a"),
              Field(FieldType.CHECKBOX, box, name="fs", choice="b"),
              Field(FieldType.CHECKBOX, box, name="ort")]  # fmt: skip
    page = PageImage(0, np.zeros((2, 2, 3), np.uint8), 1.0, 100, 100, fields=fields)
    renames = {id(f): n for f, n in ensure_unique_names(Document(Path("x.pdf"), [page]))}
    # "ort" twice = one linked text field; the checkbox "ort" clashes and is renamed.
    assert renames == {id(fields[2]): "text_p1_1", id(fields[5]): "ort_1"}


def test_auto_fill_never_overwrites_typed_values():
    from scan2form.core.autofill.session import FillSession

    partner = Profile("Partner", {"first_name": "Max", "city": "Bonn"})
    session = FillSession([text("vorname"), text("ort"), text("geburtsort")], Vault([ME, partner]))
    session.fill_from_profile(TODAY)
    assert session.value("vorname") == "Erika" and session.value("ort") == "Köln"

    session.set_value("vorname", "Eri")  # typed on the page
    session.set_value("ort", None)  # cleared on purpose
    session.profile_id = partner.id
    changed = session.fill_from_profile(TODAY)
    assert session.value("vorname") == "Eri" and session.value("ort") is None  # both kept
    assert changed == [] and session.kept == 2

    session.set_mapping("vorname", "first_name", TODAY)  # explicit choice for this field
    assert session.value("vorname") == "Max"
    session.clear()
    session.fill_from_profile(TODAY)
    assert session.value("ort") == "Bonn"


def test_values_already_in_the_pdf_are_kept(third_party_form, tmp_path):
    from scan2form.core.autofill.fill import read_form_values
    from scan2form.core.autofill.session import FillSession

    partly = tmp_path / "partly.pdf"
    fill_pdf(third_party_form, {"Text1": "Hans", "Familienstand": "ledig"}, partly)
    existing = read_form_values(partly)
    assert existing["Text1"] == "Hans" and existing["Familienstand"] == "ledig"

    session = FillSession(read_form_fields(partly), Vault([ME]))
    session.load_existing(existing)
    assert not session.dirty  # nothing new to save yet
    session.fill_from_profile(TODAY)
    assert session.value("Text1") == "Hans" and session.value("Familienstand") == "ledig"
    assert session.value("Text2") == "Heidestraße 17"  # empty fields are still filled
