import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless-safe, no display needed

from PySide6.QtGui import QUndoStack

from prismcut.core.undo_commands import AddOrRemoveItemCommand, ChangePropertiesCommand


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_change_properties_undo_redo():
    stack = QUndoStack()
    obj = Obj(x=1, y="a")
    stack.push(ChangePropertiesCommand("edit", obj, {"x": 1, "y": "a"},
                                       {"x": 2, "y": "b"}, lambda: None))
    assert (obj.x, obj.y) == (2, "b")
    stack.undo()
    assert (obj.x, obj.y) == (1, "a")
    stack.redo()
    assert (obj.x, obj.y) == (2, "b")


def test_add_or_remove_item_add():
    stack = QUndoStack()
    store: dict[str, str] = {}
    stack.push(AddOrRemoveItemCommand(
        "add", add=True,
        insert=lambda: store.__setitem__("a", "1"),
        remove=lambda: store.pop("a", None),
        refresh=lambda: None))
    assert store == {"a": "1"}
    stack.undo()
    assert store == {}
    stack.redo()
    assert store == {"a": "1"}


def test_add_or_remove_item_delete():
    stack = QUndoStack()
    store = {"a": "1"}
    stack.push(AddOrRemoveItemCommand(
        "delete", add=False,
        insert=lambda: store.__setitem__("a", "1"),
        remove=lambda: store.pop("a", None),
        refresh=lambda: None))
    assert store == {}
    stack.undo()
    assert store == {"a": "1"}
    stack.redo()
    assert store == {}


def test_macro_groups_as_one_undo_step():
    stack = QUndoStack()
    store: dict[str, str] = {"orig": "keep"}
    refreshes = []

    def refresh():
        refreshes.append(1)

    stack.beginMacro("split")
    stack.push(ChangePropertiesCommand("shrink", Obj(v=10), {"v": 10}, {"v": 5}, refresh))
    stack.push(AddOrRemoveItemCommand(
        "new half", add=True,
        insert=lambda: store.__setitem__("new", "1"),
        remove=lambda: store.pop("new", None),
        refresh=refresh))
    stack.endMacro()

    assert store == {"orig": "keep", "new": "1"}
    assert stack.count() == 1  # macro collapses to a single undo step
    stack.undo()
    assert store == {"orig": "keep"}
    stack.redo()
    assert store == {"orig": "keep", "new": "1"}


def test_refresh_called_on_both_undo_and_redo():
    calls = []
    stack = QUndoStack()
    stack.push(ChangePropertiesCommand("edit", Obj(v=1), {"v": 1}, {"v": 2},
                                       lambda: calls.append("r")))
    stack.undo()
    stack.redo()
    assert calls == ["r", "r", "r"]  # once on push (implicit redo), once on undo, once on redo
