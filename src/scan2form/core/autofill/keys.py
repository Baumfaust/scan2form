"""Catalogue of standard data keys, their synonyms and derived values.

Synonyms are written as label slugs (see core.naming.slugify) and compared
after `normalize_label`, which drops filler words and format hints, so
"Geburtsdatum (TT.MM.JJJJ)" and "Name des Antragstellers" still match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from scan2form.core.naming import slugify

DATE_FORMAT = "%d.%m.%Y"  # German default; stored values are ISO (YYYY-MM-DD)
MARITAL_CHOICES = (
    "ledig",
    "verheiratet",
    "geschieden",
    "verwitwet",
    "eingetragene_lebenspartnerschaft",
)

# Tokens that do not change which datum is meant.
_FILLER = {
    "des", "der", "die", "den", "dem", "das", "und", "bzw", "oder", "ihr", "ihre", "ihres", "ihren",
    "antragsteller", "antragstellers", "antragstellerin", "antragstellende", "person", "the", "of", "your",
    "bitte", "angeben", "falls", "vorhanden", "optional", "freiwillig", "aktuelle", "aktueller",
    "tt", "mm", "jj", "jjjj", "dd", "yyyy", "yy", "tag", "monat", "jahr", "privat", "private",
}  # fmt: skip
_GENERIC = re.compile(
    r"^(text|checkbox|signature)_p\d+_\d+$"  # Scan2Form fallback names
    r"|^(text|textfield|textfeld|field|feld|check_?box|kontrollkaestchen|optionsfeld|button|untitled|"
    r"dropdown|combo_?box|list_?box|signature|unterschrift)_?\d*$"  # typical third-party defaults
)


@dataclass(frozen=True)
class KeyDef:
    key: str
    label: str  # shown in the UI (German first; the app's forms are mostly German)
    synonyms: tuple[str, ...] = ()
    kind: str = "text"  # text | date | choice
    choices: tuple[str, ...] = ()
    derived: bool = False  # computed from other keys, not stored
    group: str = "Person"
    custom: bool = field(default=False, compare=False)


# fmt: off
CATALOGUE: tuple[KeyDef, ...] = (
    KeyDef("first_name", "Vorname", ("vorname", "vornamen", "rufname", "first_name", "given_name", "forename")),
    KeyDef("last_name", "Nachname", ("nachname", "familienname", "zuname", "last_name", "surname", "family_name")),
    KeyDef("birth_name", "Geburtsname", ("geburtsname", "maiden_name", "birth_name")),
    KeyDef("title", "Titel", ("titel", "akademischer_grad", "title")),
    KeyDef("full_name", "Vor- und Nachname", ("name", "vorname_name", "vorname_nachname", "vor_nachname",
                                              "full_name", "first_and_last_name"), derived=True),
    KeyDef("last_first", "Nachname, Vorname", ("name_vorname", "nachname_vorname", "familienname_vorname",
                                               "last_name_first_name"), derived=True),
    KeyDef("birth_date", "Geburtsdatum", ("geburtsdatum", "geb_datum", "geb_am", "geboren_am", "date_of_birth",
                                          "birth_date", "dob"), kind="date"),
    KeyDef("birth_place", "Geburtsort", ("geburtsort", "place_of_birth", "birth_place")),
    KeyDef("nationality", "Staatsangehörigkeit", ("staatsangehoerigkeit", "nationalitaet", "nationality",
                                                  "citizenship")),
    KeyDef("marital_status", "Familienstand", ("familienstand", "marital_status"), kind="choice",
           choices=MARITAL_CHOICES),
    KeyDef("street", "Straße", ("strasse", "street"), group="Adresse"),
    KeyDef("house_number", "Hausnummer", ("hausnummer", "hausnr", "haus_nr", "nr", "house_number"), group="Adresse"),
    KeyDef("street_and_number", "Straße und Hausnummer", ("strasse_hausnummer", "strasse_hausnr", "strasse_nr",
                                                          "anschrift", "adresse", "address", "street_address"),
           derived=True, group="Adresse"),
    KeyDef("postcode", "PLZ", ("plz", "postleitzahl", "zip", "zip_code", "postcode", "postal_code"), group="Adresse"),
    KeyDef("city", "Ort", ("ort", "wohnort", "stadt", "gemeinde", "city", "town"), group="Adresse"),
    KeyDef("postcode_city", "PLZ und Ort", ("plz_ort", "postleitzahl_ort", "plz_wohnort", "zip_city"),
           derived=True, group="Adresse"),
    KeyDef("country", "Land", ("land", "staat", "country"), group="Adresse"),
    KeyDef("phone", "Telefon", ("telefon", "telefonnummer", "tel", "phone", "telephone"), group="Kontakt"),
    KeyDef("mobile", "Mobil", ("mobil", "mobilnummer", "handy", "mobile", "cell"), group="Kontakt"),
    KeyDef("email", "E-Mail", ("e_mail", "email", "mail", "e_mail_adresse", "email_address"), group="Kontakt"),
    KeyDef("occupation", "Beruf", ("beruf", "berufsbezeichnung", "ausgeuebter_beruf", "taetigkeit", "occupation",
                                   "profession", "job_title"), group="Beruf"),
    KeyDef("employer", "Arbeitgeber", ("arbeitgeber", "arbeitgeberin", "employer"), group="Beruf"),
    KeyDef("iban", "IBAN", ("iban",), group="Bank"),
    KeyDef("bic", "BIC", ("bic", "swift", "bic_swift"), group="Bank"),
    KeyDef("bank", "Kreditinstitut", ("kreditinstitut", "bank", "name_der_bank", "geldinstitut"), group="Bank"),
    KeyDef("account_holder", "Kontoinhaber", ("kontoinhaber", "kontoinhaberin", "account_holder"), group="Bank"),
    KeyDef("tax_id", "Steuer-ID", ("steuer_id", "steuerliche_identifikationsnummer", "steuer_identifikationsnummer",
                                   "idnr", "tax_id"), group="Behörden"),
    KeyDef("social_security_no", "Sozialversicherungsnummer", ("sozialversicherungsnummer", "rentenversicherungsnummer",
                                                               "sv_nummer"), group="Behörden"),
    KeyDef("health_insurance", "Krankenkasse", ("krankenkasse", "krankenversicherung", "health_insurance"),
           group="Behörden"),
    KeyDef("today", "Heutiges Datum", ("datum", "date", "heute", "datum_unterschrift", "unterschriftsdatum",
                                            "datum_unterzeichnung", "unterzeichnet_am", "signature_date",
                                            "date_signature", "date_signed"), kind="date", derived=True, group="Unterschrift"),
    KeyDef("place_date", "Ort, Datum", ("ort_datum", "datum_ort", "ort_datum_unterschrift", "place_date",
                                              "place_and_date", "place_date_signature"), derived=True,
           group="Unterschrift"),
)
# fmt: on
BY_KEY = {k.key: k for k in CATALOGUE}


def normalize_label(text: str) -> str:
    """Comparable form of a field label/name: slug without filler tokens and dedup suffixes."""
    slug = re.sub(r"_\d+$", "", slugify(text))  # "vorname_2" (deduplicated) -> "vorname"
    tokens = [t for t in slug.split("_") if t and t not in _FILLER]
    return "_".join(tokens)


def is_generic(name: str) -> bool:
    """Names that carry no meaning ("text_p2_7", "Text1", "Check Box3")."""
    return not slugify(name) or bool(_GENERIC.match(slugify(name)))


_SYNONYMS: dict[str, str] = {}
for _k in CATALOGUE:
    for _s in _k.synonyms:
        _SYNONYMS.setdefault(normalize_label(_s), _k.key)
for _choice in MARITAL_CHOICES:
    _SYNONYMS.setdefault(_choice, f"marital_status={_choice}")


def catalogue_match(label: str) -> str | None:
    """Catalogue key for a label, or 'marital_status=<choice>' for status checkboxes."""
    return _SYNONYMS.get(normalize_label(label))


_GERMAN_DATE = re.compile(r"^\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*$")


def parse_date(text: str) -> str:
    """'12.08.1964' -> '1964-08-12' (the stored form); anything else is kept as typed."""
    m = _GERMAN_DATE.match(text)
    return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}" if m else text.strip()


def format_date(value: str, fmt: str = DATE_FORMAT) -> str:
    try:
        return date.fromisoformat(value).strftime(fmt)
    except ValueError:
        return value  # free text stays as typed


def resolve(
    key: str, values: dict[str, str], today: date | None = None, fmt: str = DATE_FORMAT
) -> str:
    """Value for a key from a profile's stored values, computing derived keys."""
    today = today or date.today()

    def get(k: str) -> str:
        return (
            resolve(k, values, today, fmt)
            if BY_KEY.get(k, KeyDef(k, k)).derived
            else values.get(k, "").strip()
        )

    if key == "full_name":
        return " ".join(p for p in (get("title"), get("first_name"), get("last_name")) if p)
    if key == "last_first":
        last, first = get("last_name"), get("first_name")
        return f"{last}, {first}" if last and first else last or first
    if key == "street_and_number":
        return " ".join(p for p in (get("street"), get("house_number")) if p)
    if key == "postcode_city":
        return " ".join(p for p in (get("postcode"), get("city")) if p)
    if key == "today":
        return today.strftime(fmt)
    if key == "place_date":
        return ", ".join(p for p in (get("city"), today.strftime(fmt)) if p)
    value = values.get(key, "").strip()
    return format_date(value, fmt) if BY_KEY.get(key) and BY_KEY[key].kind == "date" else value
