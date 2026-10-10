"""Tree widget helper utilities for safe traversal and clearing in PySide6."""

from typing import Iterator, Union
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


def iter_tree_items(root: Union[QTreeWidget, QTreeWidgetItem]) -> Iterator[QTreeWidgetItem]:
    """Safely traverse all descendant QTreeWidgetItem nodes without allocating
    a C++ QTreeWidgetItemIterator (which registers internal iterators in Qt that
    can cause segmentation faults during model modification or teardown).
    """
    if isinstance(root, QTreeWidget):
        for i in range(root.topLevelItemCount()):
            top = root.topLevelItem(i)
            if top is not None:
                yield top
                yield from iter_tree_items(top)
    else:
        for i in range(root.childCount()):
            child = root.child(i)
            if child is not None:
                yield child
                yield from iter_tree_items(child)


def safe_clear_tree(tree: QTreeWidget) -> None:
    """Safely clear a QTreeWidget avoiding PySide6 / Shiboken segmentation faults.

    Clears current item and selection first to prevent dangling selection model
    state, blocks signals during teardown, cleanly removes top-level items via
    takeTopLevelItem so that child item lifetimes are cleanly managed, and then
    resets tree signals.
    """
    tree.blockSignals(True)
    if tree.selectionModel():
        tree.selectionModel().clearSelection()
    tree.setCurrentItem(None)
    while tree.topLevelItemCount() > 0:
        item = tree.takeTopLevelItem(0)
        del item
    tree.clear()
    tree.blockSignals(False)
