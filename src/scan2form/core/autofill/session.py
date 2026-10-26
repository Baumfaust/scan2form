"""State of filling out one form in the app: values, mappings and what to remember.

Values are never stored in the project file. What the user types is kept as an
encrypted draft in the vault (restored when the form is opened again), and typed
values for labelled fields are learned into the profile right away.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from scan2form.core.autofill.fill import fill_pdf, read_form_fields
from scan2form.core.autofill.keys import BY_KEY, parse_date
from scan2form.core.autofill.mapping import (
    FormField,
    Mapping,
    add_custom_key,
    collect_suggestions,
    key_label,
    learn,
    map_fields,
    value_for,
)
from scan2form.core.autofill.vault import Profile, Vault
from scan2form.core.model import FieldType
from scan2form.core.pipeline import Document, form_template

Value = str | bool


def form_fields_of(document: Document) -> list[FormField]:
    """The fields as a PDF viewer will see them: one per name, radio widgets merged.

    Labels (/TU) and dropdown options come from the source while it is a fillable PDF.
    """
    from_source = {}
    if document.has_source_fields and document.source.suffix.lower() == ".pdf":
        from_source = {f.id: f for f in read_form_fields(document.source)}
    result: dict[str, FormField] = {}
    for page in document.pages:
        band = 3 * page.pt_per_mm  # reading order: rows top to bottom, then left to right
        for f in sorted(page.fields, key=lambda f: (round(f.bbox.y0 / band), f.bbox.x0)):
            if not f.name or f.type is FieldType.SIGNATURE:
                continue
            known = from_source.get(f.name)
            if f.choice is not None:
                previous = result.get(f.name)
                choices = (*previous.choices, f.choice) if previous else (f.choice,)
                label = known.label if known else f.name
                result[f.name] = FormField(f.name, f.name, label, "radio", choices)
            elif f.name not in result:
                kind = known.kind if known and known.kind in ("choice", "text") else f.type.value
                if f.type is FieldType.CHECKBOX:
                    kind = "checkbox"
                result[f.name] = FormField(f.name, f.name, known.label if known else f.name, kind,
                                           known.choices if known else ())  # fmt: skip
    return list(result.values())


def draft_key(document: Document) -> str:
    return str(document.source.resolve())


class FillSession:
    def __init__(
        self, fields: list[FormField], vault: Vault, profile_id: str | None = None
    ) -> None:
        self.fields = fields
        self.by_id = {f.id: f for f in fields}
        self.vault = vault
        self.profile_id = profile_id or vault.profiles[0].id
        self.mappings: dict[str, Mapping | None] = map_fields(fields, vault)
        self.values: dict[str, Value] = {}
        # Fields the user typed, ticked or cleared (or that came filled in the PDF):
        # automatic filling never touches them.
        self.typed: set[str] = set()
        # Typed values that differ from data already in the profile: asked before updating.
        self.pending_updates: dict[str, tuple[str, str]] = {}  # field id -> (key, new value)
        self.dirty = False  # values changed since the last filled copy
        self.output: Path | None = None  # where the last filled copy was saved

    @property
    def profile(self) -> Profile:
        return self.vault.profile(self.profile_id)

    def value(self, field_id: str) -> Value | None:
        return self.values.get(field_id)

    def set_value(self, field_id: str, value: Value | None, typed: bool = True) -> None:
        """`typed=False` marks a value written by automatic filling (it may be replaced)."""
        if typed:
            self.typed.add(field_id)
        else:
            self.typed.discard(field_id)
            self.pending_updates.pop(field_id, None)
        if value in (None, "", False) and field_id in self.values:
            del self.values[field_id]
            self.dirty = True
        elif value not in (None, "", False) and self.values.get(field_id) != value:
            self.values[field_id] = value
            self.dirty = True

    def profile_value(self, field_id: str, today: date | None = None) -> Value | None:
        f = self.by_id[field_id]
        return value_for(f, self.mappings.get(field_id), self.profile, today or date.today())

    def load_existing(self, values: dict[str, Value]) -> None:
        """Values already in the opened PDF: shown, kept, never overwritten automatically."""
        for fid, value in values.items():
            if fid in self.by_id and value not in (None, "", False):
                self.values[fid] = value
                self.typed.add(fid)

    def fill_from_profile(self, today: date | None = None) -> list[str]:
        """Put the profile's data into mapped fields the user has not typed into;
        returns the ids that changed. Earlier automatic values follow the new data."""
        changed = []
        for f in self.fields:
            if self.mappings.get(f.id) is None or f.id in self.typed:
                continue
            new = self.profile_value(f.id, today)
            if new != self.values.get(f.id) and not (
                new in (None, False) and f.id not in self.values
            ):
                self.set_value(f.id, new, typed=False)
                changed.append(f.id)
        return changed

    @property
    def kept(self) -> int:
        """Mapped fields left alone by automatic filling because the user filled them."""
        return sum(1 for fid in self.typed if self.mappings.get(fid) is not None)

    def clear(self) -> list[str]:
        changed = list(self.values)
        self.values.clear()
        self.typed.clear()
        self.pending_updates.clear()
        self.dirty = bool(changed) or self.dirty
        return changed

    # -- learning ------------------------------------------------------------------------
    def set_mapping(self, field_id: str, code: str | None, today: date | None = None) -> None:
        """Change what a field is filled from; remembered for every form with this label."""
        mapping = Mapping.decode(code, "manual") if code else None
        self.mappings[field_id] = mapping
        learn(self.vault, self.by_id[field_id], mapping)
        if code:
            # An explicit choice for this one field: show that datum, and follow it from now on.
            self.set_value(field_id, self.profile_value(field_id, today), typed=False)

    def learn_typed(self, field_id: str) -> str | None:
        """Store a value typed into a labelled text field in the profile.

        New data (no match yet, or the matched datum is still empty) is stored at once;
        returns its label. A value that differs from stored data waits in
        `pending_updates` until the user confirms it. Generic names ("text_p2_7")
        carry no meaning and are never learned.
        """
        f = self.by_id.get(field_id)
        value = self.values.get(field_id)
        if f is None or f.kind != "text" or not f.slug or not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        mapping = self.mappings.get(field_id)
        if mapping is None:
            custom = next((c for c in self.vault.custom_keys if c.label == f.label), None)
            custom = custom or add_custom_key(self.vault, f.label)
            mapping = Mapping(custom.key, source="learned")
            self.mappings[field_id] = mapping
            learn(self.vault, f, mapping)
        key_def = BY_KEY.get(mapping.key)
        if mapping.choice or (key_def is not None and key_def.derived):
            return None  # combined values (full name, Ort/Datum) are not split back
        stored = parse_date(value) if key_def is not None and key_def.kind == "date" else value
        current = self.profile.values.get(mapping.key, "")
        if current == stored:
            self.pending_updates.pop(field_id, None)
            return None
        if current:
            self.pending_updates[field_id] = (mapping.key, stored)
            return None
        self.profile.values[mapping.key] = stored
        return key_label(self.vault, mapping.key)

    def describe_pending(self) -> list[str]:
        lines = []
        for key, new in dict(self.pending_updates.values()).items():
            old = self.profile.values.get(key, "")
            lines.append(f"{key_label(self.vault, key)}: “{old}” → “{new}”")
        return lines

    def apply_pending(self) -> int:
        updates = dict(self.pending_updates.values())
        self.profile.values.update(updates)
        self.pending_updates.clear()
        return len(updates)

    # -- drafts --------------------------------------------------------------------------
    def to_draft(self) -> dict:
        """What the user entered by hand (profile values are re-created on their own)."""
        return {
            "profile_id": self.profile_id,
            "values": {fid: v for fid, v in self.values.items() if fid in self.typed},
            "cleared": sorted(fid for fid in self.typed if fid not in self.values),
            "output": str(self.output) if self.output else None,
        }

    def apply_draft(self, draft: dict) -> int:
        """Restore a draft; returns how many typed values came back."""
        if draft.get("profile_id") in {p.id for p in self.vault.profiles}:
            self.profile_id = draft["profile_id"]
        restored = 0
        for fid, value in draft.get("values", {}).items():
            if fid in self.by_id:
                self.values[fid] = value
                self.typed.add(fid)
                restored += 1
        self.typed.update(fid for fid in draft.get("cleared", []) if fid in self.by_id)
        if draft.get("output"):
            self.output = Path(draft["output"])
        self.dirty = restored > 0
        return restored

    def carry_over(self, previous: FillSession) -> None:
        """Keep values from an earlier session (fields edited in Prepare mode)."""
        self.profile_id = previous.profile_id
        for fid, value in previous.values.items():
            if fid in self.by_id:
                self.values[fid] = value
        self.typed = previous.typed & self.by_id.keys()
        self.pending_updates = {
            fid: u for fid, u in previous.pending_updates.items() if fid in self.by_id
        }
        self.output = previous.output
        self.dirty = previous.dirty

    def remember(self, form_name: str) -> None:
        """After saving a copy: note labelled fields that still have no data as suggestions."""
        collect_suggestions(self.vault, self.fields, map_fields(self.fields, self.vault), form_name)

    def write_copy(self, document: Document, output: Path) -> int:
        """Fill a blank template of `document` with the current values; returns fields filled."""
        if output.resolve() == document.source.resolve():
            raise ValueError("Refusing to overwrite the source; filling always writes a copy.")
        values = {fid: v for fid, v in self.values.items() if fid in self.by_id}
        for f in self.fields:  # unticked checkboxes must be written as "Off", too
            if f.kind == "checkbox" and f.id not in values:
                values[f.id] = False
        count = fill_pdf(form_template(document), values, output)
        self.dirty = False
        self.output = output
        return count
