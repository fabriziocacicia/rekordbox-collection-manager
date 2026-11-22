"""Unit tests for StoresView and StoreCardWidget."""

import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import create_engine

from rekordbox_collection_manager.gui.stores_view import (
    StoreCardWidget,
    StoreMetricBox,
    StoreRatioBar,
    StoresView,
)
from rekordbox_collection_manager.gui.workers import StoresSummaryWorker
from rekordbox_collection_manager.utils import (
    KNOWN_STORES,
    STORE_BANDCAMP,
    STORE_BEATPORT,
    STORE_QOBUZ,
    STORE_TRAXSOURCE,
)

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestStoresView(unittest.TestCase):
    def setUp(self):
        self.view = StoresView()

    def tearDown(self):
        self.view.cleanup()
        self.view.close()

    def test_stores_view_initial_ui(self):
        self.assertEqual(len(self.view.cards), 4)
        for store in KNOWN_STORES:
            self.assertIn(store, self.view.cards)
            card = self.view.cards[store]
            self.assertEqual(card.purchased_count, 0)
            self.assertEqual(card.to_buy_count, 0)
            self.assertEqual(card.total_badge.text(), "0 tracks")

    def test_store_card_widget_counts_and_signals(self):
        card = StoreCardWidget(STORE_BEATPORT, icon="🎧")
        card.set_counts(15, 4)

        self.assertEqual(card.purchased_count, 15)
        self.assertEqual(card.to_buy_count, 4)
        self.assertEqual(card.total_badge.text(), "19 tracks")
        self.assertEqual(card.box_purchased.count_label.text(), "15")
        self.assertEqual(card.box_to_buy.count_label.text(), "4")
        self.assertEqual(card.ratio_bar.purchased, 15)
        self.assertEqual(card.ratio_bar.to_buy, 4)
        self.assertIn("79%", card.ratio_bar.toolTip())

        # Test signals
        received = []
        card.navigate_requested.connect(lambda status, store: received.append((status, store)))

        card.box_purchased.clicked.emit()
        self.assertEqual(received, [("purchased", STORE_BEATPORT)])

        card.box_to_buy.clicked.emit()
        self.assertEqual(received, [("purchased", STORE_BEATPORT), ("purchasable", STORE_BEATPORT)])

    def test_store_card_zero_count_state(self):
        card = StoreCardWidget(STORE_QOBUZ, icon="🎼")
        card.set_counts(0, 0)
        self.assertEqual(card.total_badge.text(), "0 tracks")
        self.assertEqual(card.box_purchased.action_label.text(), "No tracks")
        self.assertEqual(card.box_to_buy.action_label.text(), "No tracks")

        # Zero-count box should not emit clicked on mousePressEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF
        received = []
        card.box_purchased.clicked.connect(lambda: received.append("clicked"))
        press_event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        card.box_purchased.mousePressEvent(press_event)
        self.assertEqual(received, [])

        # Non-zero count box SHOULD emit clicked on mousePressEvent
        card.set_counts(5, 0)
        card.box_purchased.mousePressEvent(press_event)
        self.assertEqual(received, ["clicked"])

    def test_store_ratio_bar(self):
        bar = StoreRatioBar()
        self.assertEqual(bar.purchased, 0)
        self.assertEqual(bar.to_buy, 0)

        # Zero count safe handling (no division by zero)
        bar.set_counts(0, 0)
        self.assertEqual(bar.purchased, 0)
        self.assertEqual(bar.to_buy, 0)
        self.assertIn("0%", bar.toolTip())

        # Proportional ratio calculation
        bar.set_counts(10, 10)
        self.assertEqual(bar.purchased, 10)
        self.assertEqual(bar.to_buy, 10)
        self.assertIn("50%", bar.toolTip())
        self.assertIn("Purchased: 10 (50%) • To Buy: 10", bar.toolTip())

        # 100% purchased
        bar.set_counts(25, 0)
        self.assertIn("100%", bar.toolTip())

        # Negative clamping
        bar.set_counts(-5, -3)
        self.assertEqual(bar.purchased, 0)
        self.assertEqual(bar.to_buy, 0)

    def test_stores_view_on_counts_ready(self):
        mock_counts = {
            STORE_BEATPORT: {"purchased": 50, "to_buy": 10},
            STORE_BANDCAMP: {"purchased": 30, "to_buy": 5},
            STORE_QOBUZ: {"purchased": 12, "to_buy": 2},
            STORE_TRAXSOURCE: {"purchased": 8, "to_buy": 1},
        }
        self.view._on_counts_ready(mock_counts)

        bp_card = self.view.cards[STORE_BEATPORT]
        self.assertEqual(bp_card.purchased_count, 50)
        self.assertEqual(bp_card.to_buy_count, 10)
        self.assertEqual(bp_card.total_badge.text(), "60 tracks")

        bc_card = self.view.cards[STORE_BANDCAMP]
        self.assertEqual(bc_card.purchased_count, 30)
        self.assertEqual(bc_card.to_buy_count, 5)

        self.assertIn("100 purchased", self.view.status_label.text())
        self.assertIn("18 to buy", self.view.status_label.text())

    def test_stores_view_open_tracks_by_store_signal(self):
        received = []
        self.view.open_tracks_by_store.connect(
            lambda status, store: received.append((status, store))
        )

        card = self.view.cards[STORE_BANDCAMP]
        card.box_to_buy.clicked.emit()

        self.assertEqual(received, [("purchasable", STORE_BANDCAMP)])

    def test_stores_summary_worker(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE djmdContent (
                    ID VARCHAR(255) PRIMARY KEY,
                    Title VARCHAR(255),
                    DJPlayCount INTEGER,
                    FolderPath VARCHAR(255),
                    Commnt TEXT,
                    FileType INTEGER DEFAULT 1,
                    rb_local_deleted INTEGER DEFAULT 0
                );
            """)
            conn.exec_driver_sql("""
                INSERT INTO djmdContent (ID, Title, DJPlayCount, FolderPath, Commnt)
                VALUES ('1', 'T1', 5, '/Music/Purchased/1.aiff', 'Purchased at Beatport.com');
            """)

        worker = StoresSummaryWorker(engine)
        results = []
        worker.success.connect(lambda counts: results.append(counts))
        worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][STORE_BEATPORT]["purchased"], 1)
        self.assertEqual(results[0][STORE_BEATPORT]["to_buy"], 0)


if __name__ == "__main__":
    unittest.main()
