"""Track Details View Dialog & View re-exports.

Provides backward-compatible imports for TrackDetailView and TrackDetailDialog.
"""

from rekordbox_collection_manager.gui.track_detail_view import (
    TrackDetailDialog,
    TrackDetailView,
    format_file_size,
    format_rating_stars,
)

__all__ = [
    "TrackDetailDialog",
    "TrackDetailView",
    "format_file_size",
    "format_rating_stars",
]
