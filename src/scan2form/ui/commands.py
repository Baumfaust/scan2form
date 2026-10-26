"""Undoable edits. `Field` objects in the Document are the committed state;
commands change them and then ask the scene to refresh the matching items."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QUndoCommand

from scan2form.core.model import BBox, Field, Orientation

if TYPE_CHECKING:
    from scan2form.ui.page_view import PageScene


class AddFields(QUndoCommand):
    def __init__(self, scene: PageScene, fields: list[Field], text: str = "Add field") -> None:
        super().__init__(text)
        self.scene, self.fields = scene, fields

    def redo(self) -> None:
        self.scene.page.fields.extend(self.fields)
        self.scene.add_items(self.fields, select=True)

    def undo(self) -> None:
        for f in self.fields:
            self.scene.page.fields.remove(f)
        self.scene.remove_items(self.fields)


class RemoveFields(QUndoCommand):
    def __init__(self, scene: PageScene, fields: list[Field]) -> None:
        super().__init__(f"Delete {len(fields)} field(s)")
        self.scene, self.fields = scene, fields
        self.positions = [scene.page.fields.index(f) for f in fields]

    def redo(self) -> None:
        for f in self.fields:
            self.scene.page.fields.remove(f)
        self.scene.remove_items(self.fields)

    def undo(self) -> None:
        for position, f in sorted(zip(self.positions, self.fields), key=lambda p: p[0]):
            self.scene.page.fields.insert(position, f)
        self.scene.add_items(self.fields, select=True)


class EditGeometry(QUndoCommand):
    def __init__(self, scene: PageScene, changes: list[tuple[Field, BBox, BBox]]) -> None:
        super().__init__(
            "Move/resize field" if len(changes) == 1 else f"Move {len(changes)} fields"
        )
        self.scene, self.changes = scene, changes

    def _apply(self, index: int) -> None:
        for change in self.changes:
            change[0].bbox = change[index]
            self.scene.refresh(change[0])

    def redo(self) -> None:
        self._apply(2)

    def undo(self) -> None:
        self._apply(1)


class EditProperties(QUndoCommand):
    """Change name/type/multiline of one field. `new` holds only the changed attributes."""

    def __init__(self, scene: PageScene, field: Field, **new) -> None:
        super().__init__("Edit field properties")
        self.scene, self.field = scene, field
        self.old = {key: getattr(field, key) for key in new}
        self.new = new

    def _apply(self, values: dict) -> None:
        for key, value in values.items():
            setattr(self.field, key, value)
        self.scene.refresh(self.field)

    def redo(self) -> None:
        self._apply(self.new)

    def undo(self) -> None:
        self._apply(self.old)


class ReorientPage(QUndoCommand):
    """Turn/straighten one page. Slider steps of one drag (same `session`) merge into one undo step."""

    MERGE_ID = 4242

    def __init__(self, scene: PageScene, old: Orientation, new: Orientation, mergeable: bool = False,
                 session: int = 0) -> None:  # fmt: skip
        super().__init__(
            "Straighten page" if old.quarter_turns == new.quarter_turns else "Rotate page"
        )
        self.scene, self.old, self.new = scene, old, new
        self.mergeable, self.session = mergeable, session

    def id(self) -> int:
        return self.MERGE_ID if self.mergeable else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (
            not isinstance(other, ReorientPage)
            or other.scene is not self.scene
            or not other.mergeable
        ):
            return False
        if other.session != self.session:  # a new drag/edit starts a new undo step
            return False
        self.new = other.new
        return True

    def redo(self) -> None:
        self.scene.set_orientation(self.new)

    def undo(self) -> None:
        self.scene.set_orientation(self.old)
