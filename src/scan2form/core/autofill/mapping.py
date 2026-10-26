"""Map form fields onto vault keys, compute fill values, collect suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from scan2form.core.autofill.keys import (
    BY_KEY,
    CATALOGUE,
    catalogue_match,
    is_generic,
    normalize_label,
    resolve,
)
from scan2form.core.autofill.vault import CustomKey, Profile, Suggestion, Vault

TRUTHY = {"1", "x", "ja", "yes", "true", "wahr", "on"}


@dataclass(frozen=True)
class FormField:
    id: str  # fully qualified PDF field name
    name: str
    label: str  # tooltip (/TU) if the form has one, else the name
    kind: str  # text | checkbox | radio | choice | signature
    choices: tuple[str, ...] = ()  # radio states / dropdown options, without "/" prefix

    @property
    def slug(self) -> str:
        """Normalized label used for matching; falls back to the name for cryptic tooltips."""
        for text in (self.label, self.name):
            if text and not is_generic(text) and normalize_label(text):
                return normalize_label(text)
        return ""


@dataclass(frozen=True)
class Mapping:
    key: str
    choice: str | None = None  # checkbox is ticked when the key's value equals this
    source: str = "catalogue"  # learned | catalogue | custom | manual

    def encode(self) -> str:
        return f"{self.key}={self.choice}" if self.choice else self.key

    @classmethod
    def decode(cls, text: str, source: str) -> Mapping:
        key, _, choice = text.partition("=")
        return cls(key, choice or None, source)


def all_keys(vault: Vault) -> list[tuple[str, str]]:
    """(key, label) for every key a field can be mapped to: catalogue first, then custom keys."""
    return [(k.key, k.label) for k in CATALOGUE] + [(c.key, c.label) for c in vault.custom_keys]


def key_label(vault: Vault, key: str) -> str:
    if key in BY_KEY:
        return BY_KEY[key].label
    custom = vault.custom_key(key)
    return custom.label if custom else key


def map_field(field: FormField, vault: Vault) -> Mapping | None:
    if field.kind == "signature" or not field.slug:
        return None
    slug = field.slug
    if slug in vault.learned:
        mapping = Mapping.decode(vault.learned[slug], "learned")
    elif (match := catalogue_match(slug)) is not None:
        mapping = Mapping.decode(match, "catalogue")
    elif (
        custom := next((c for c in vault.custom_keys if normalize_label(c.label) == slug), None)
    ) is not None:
        mapping = Mapping(custom.key, None, "custom")
    else:
        return None
    if mapping.choice and field.kind != "checkbox":
        return None  # "verheiratet" as a text field is not the status checkbox
    if field.kind == "checkbox" and not mapping.choice and mapping.source == "catalogue":
        return None  # a checkbox labelled "Vorname" cannot be ticked from a name
    return mapping


def map_fields(fields: list[FormField], vault: Vault) -> dict[str, Mapping | None]:
    return {f.id: map_field(f, vault) for f in fields}


def value_for(
    field: FormField, mapping: Mapping | None, profile: Profile, today: date | None = None
):
    """str for text-like fields, bool for checkboxes, state name for radios; None = leave empty."""
    if mapping is None:
        return None
    text = resolve(mapping.key, profile.values, today)
    if field.kind == "checkbox":
        if mapping.choice:
            return profile.values.get(mapping.key, "") == mapping.choice
        return text.strip().lower() in TRUTHY
    if field.kind in ("radio", "choice"):
        wanted = normalize_label(text)
        return next((c for c in field.choices if normalize_label(c) == wanted), None)
    return text or None


def compute_values(fields: list[FormField], mappings: dict[str, Mapping | None], profile: Profile,
                   today: date | None = None) -> dict[str, object]:  # fmt: skip
    values = {f.id: value_for(f, mappings.get(f.id), profile, today) for f in fields}
    return {k: v for k, v in values.items() if v is not None}


def learn(vault: Vault, field: FormField, mapping: Mapping | None) -> None:
    """Remember a manual mapping for every future field with the same label (None forgets it)."""
    if not field.slug:
        return
    if mapping is None:
        vault.learned.pop(field.slug, None)
    else:
        vault.learned[field.slug] = mapping.encode()


def add_custom_key(vault: Vault, label: str) -> CustomKey:
    slug = normalize_label(label) or "feld"
    key, n = f"c_{slug}", 2
    while vault.custom_key(key) is not None:
        key, n = f"c_{slug}_{n}", n + 1
    custom = CustomKey(key, label.strip())
    vault.custom_keys.append(custom)
    return custom


def collect_suggestions(vault: Vault, fields: list[FormField], mappings: dict[str, Mapping | None],
                        form_name: str) -> int:  # fmt: skip
    """Remember labelled, unmapped text fields as suggestions; returns how many are new."""
    new = 0
    for f in fields:
        slug = f.slug
        if (
            f.kind != "text"
            or mappings.get(f.id) is not None
            or not slug
            or slug in vault.dismissed
        ):
            continue
        existing = next((s for s in vault.suggestions if s.slug == slug), None)
        if existing is None:
            vault.suggestions.append(
                Suggestion(slug, f.label if not is_generic(f.label) else f.name, 1, form_name)
            )
            new += 1
        elif existing.last_form != form_name:
            existing.count += 1
            existing.last_form = form_name
    return new


def accept_suggestion(vault: Vault, slug: str) -> CustomKey:
    suggestion = next(s for s in vault.suggestions if s.slug == slug)
    vault.suggestions.remove(suggestion)
    custom = add_custom_key(vault, suggestion.label)
    vault.learned[slug] = custom.key
    return custom


def dismiss_suggestion(vault: Vault, slug: str) -> None:
    vault.suggestions = [s for s in vault.suggestions if s.slug != slug]
    if slug not in vault.dismissed:
        vault.dismissed.append(slug)
