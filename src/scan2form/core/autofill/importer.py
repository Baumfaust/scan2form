"""Import personal data from a simple text/Markdown list into a vault profile.

Accepted lines (one datum each; headings and prose are ignored):

    - **First Name (`Vorname`):** Max
    Geburtsdatum: 12.08.1964
    | PLZ | 12345 |

Every label variant on a line (the text and each `backticked` form label) is
tried against the catalogue; a datum that matches nothing becomes a custom field
named after its form label, so forms using that label are filled later.
Computed keys are split into their parts ("Straße" with a house number, full
name, PLZ + Ort) or skipped ("Ort, Datum" always uses today's date).

Run `python -m scan2form.core.autofill.importer data.md [--profile Ich]` to
import into the encrypted vault; it reports keys only, never values.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field

from scan2form.core.autofill.keys import (
    BY_KEY,
    MARITAL_CHOICES,
    catalogue_match,
    normalize_label,
    parse_date,
)
from scan2form.core.autofill.mapping import add_custom_key
from scan2form.core.autofill.vault import Profile, Vault, VaultStore

_LINE = re.compile(r"^\s*(?:[-*+]|\d+\.)?\s*(?P<label>[^:|]+?)\s*:\s*(?P<value>.*?)\s*$")
_BACKTICKED = re.compile(r"`([^`]+)`")
_HOUSE_NUMBER = re.compile(
    r"^(?P<street>.*\D)\s+(?P<number>\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)$"
)


@dataclass
class ImportResult:
    stored: list[str] = field(default_factory=list)  # keys that received a value
    created: list[str] = field(default_factory=list)  # labels of new custom fields
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)


def parse_entries(text: str) -> list[tuple[list[str], str]]:
    """(label variants, value) per data line; the first variant is the preferred form label."""
    entries = []
    header = None  # entry from the previous table row; a separator line below makes it the header
    for raw in text.splitlines():
        line = raw.replace("**", "").replace("__", "").strip()
        previous, header = header, None
        if not line or line.startswith("#"):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                if previous is not None and previous in entries:
                    entries.remove(previous)
                continue
            if len(cells) != 2 or not cells[1]:
                continue
            label, value = cells
        elif m := _LINE.match(line):
            label, value = m["label"], m["value"]
        else:
            continue
        forms = _BACKTICKED.findall(label)
        plain = re.sub(r"\(.*?\)", "", _BACKTICKED.sub("", label)).strip(" /")
        variants = [v.strip() for v in [*forms, plain] if v.strip()]
        if variants and value:
            entries.append((variants, value))
            if line.startswith("|"):
                header = entries[-1]
    return entries


def _split_street(value: str) -> dict[str, str]:
    m = _HOUSE_NUMBER.match(value)
    return (
        {"street": m["street"].strip(" ,"), "house_number": m["number"]} if m else {"street": value}
    )


def _key_values(key: str, value: str) -> dict[str, str] | str:
    """Stored values for a catalogue key, or a skip reason."""
    if key in ("street", "street_and_number"):
        return _split_street(value)
    if key == "full_name":
        first, _, last = value.rpartition(" ")
        return {"first_name": first, "last_name": last} if first else {"last_name": last}
    if key == "last_first":
        last, _, first = value.partition(",")
        return {"last_name": last.strip(), "first_name": first.strip()}
    if key == "postcode_city":
        postcode, _, city = value.partition(" ")
        return (
            {"postcode": postcode, "city": city.strip()} if postcode.isdigit() else {"city": value}
        )
    if key in ("today", "place_date"):
        return "always filled with today's date"
    if key == "marital_status":
        choice = normalize_label(value)
        return {key: choice} if choice in MARITAL_CHOICES else "unknown marital status"
    if BY_KEY[key].kind == "date":
        return {key: parse_date(value)}
    return {key: value}


def import_entries(
    vault: Vault, profile: Profile, entries: list[tuple[list[str], str]]
) -> ImportResult:
    """Write the entries into `profile` (overwriting), creating custom fields as needed."""
    result = ImportResult()
    pending: dict[str, str] = {}
    explicit: set[str] = set()
    for variants, value in entries:
        matches = [m for v in variants if (m := catalogue_match(v)) and "=" not in m]
        # Prefer a stored key: "Name / Nachname" means the last name, not the computed full name.
        key = next((m for m in matches if not BY_KEY[m].derived), matches[0] if matches else None)
        if key is None:
            slug = normalize_label(variants[0])
            custom = next((c for c in vault.custom_keys if normalize_label(c.label) == slug), None)
            if custom is None:
                custom = add_custom_key(vault, variants[0])
                result.created.append(custom.label)
            pending[custom.key] = value
            explicit.add(custom.key)
            continue
        values = _key_values(key, value)
        if isinstance(values, str):
            result.skipped.append((variants[0], values))
            continue
        for k, v in values.items():
            # A separately given part ("Hausnummer: 5") wins over one split off a combined value.
            if v and (k not in explicit or len(values) == 1):
                pending[k] = v
        if len(values) == 1:
            explicit.update(values)
    profile.values.update(pending)
    result.stored = list(pending)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Import personal data into the encrypted Scan2Form vault."
    )
    parser.add_argument("file", help="text/Markdown file with 'Label: value' lines")
    parser.add_argument("--profile", default=None, help="profile name (default: the first profile)")
    args = parser.parse_args(argv)

    store = VaultStore()
    vault = store.load()
    if args.profile is None:
        profile = vault.profiles[0]
    else:
        profile = next((p for p in vault.profiles if p.name == args.profile), None)
        if profile is None:
            profile = Profile(args.profile)
            vault.profiles.append(profile)
    with open(args.file, encoding="utf-8") as fh:
        result = import_entries(vault, profile, parse_entries(fh.read()))
    store.save(vault)
    labels = [BY_KEY[k].label if k in BY_KEY else vault.custom_key(k).label for k in result.stored]
    print(f"Profile “{profile.name}”: {len(result.stored)} values stored ({', '.join(labels)})")
    if result.created:
        print(f"New custom fields: {', '.join(result.created)}")
    for label, reason in result.skipped:
        print(f"Skipped “{label}”: {reason}")


if __name__ == "__main__":
    main()
