"""Tests for tree_utils helper functions."""

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt

from rekordbox_collection_manager.gui.tree_utils import iter_tree_items, safe_clear_tree


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_iter_tree_items_empty(qapp):
    tree = QTreeWidget()
    items = list(iter_tree_items(tree))
    assert items == []


def test_iter_tree_items_hierarchy(qapp):
    tree = QTreeWidget()
    parent1 = QTreeWidgetItem(["Parent 1"])
    parent2 = QTreeWidgetItem(["Parent 2"])
    tree.addTopLevelItem(parent1)
    tree.addTopLevelItem(parent2)

    child1_1 = QTreeWidgetItem(["Child 1.1"])
    child1_2 = QTreeWidgetItem(["Child 1.2"])
    parent1.addChild(child1_1)
    parent1.addChild(child1_2)

    subchild = QTreeWidgetItem(["SubChild 1.1.1"])
    child1_1.addChild(subchild)

    items = list(iter_tree_items(tree))
    assert len(items) == 5
    texts = [item.text(0) for item in items]
    assert texts == ["Parent 1", "Child 1.1", "SubChild 1.1.1", "Child 1.2", "Parent 2"]


def test_safe_clear_tree(qapp):
    tree = QTreeWidget()
    for p in range(3):
        parent = QTreeWidgetItem([f"Parent {p}"])
        tree.addTopLevelItem(parent)
        for c in range(5):
            child = QTreeWidgetItem([f"Child {p}.{c}"])
            parent.addChild(child)

    tree.setCurrentItem(parent)
    assert tree.topLevelItemCount() == 3

    safe_clear_tree(tree)
    assert tree.topLevelItemCount() == 0
    assert tree.currentItem() is None
    assert list(iter_tree_items(tree)) == []
