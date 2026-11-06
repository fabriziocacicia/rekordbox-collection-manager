"""Interactive analytics widgets package."""

from rekordbox_collection_manager.gui.analytics_widgets.camelot_wheel_widget import CamelotWheelWidget
from rekordbox_collection_manager.gui.analytics_widgets.crate_health_widget import CrateHealthMatrixWidget
from rekordbox_collection_manager.gui.analytics_widgets.survival_widget import SurvivalDecayWidget
from rekordbox_collection_manager.gui.analytics_widgets.transition_graph_widget import TransitionGraphWidget
from rekordbox_collection_manager.gui.analytics_widgets.turnover_widget import SetTurnoverWidget

__all__ = [
    "CrateHealthMatrixWidget",
    "CamelotWheelWidget",
    "SetTurnoverWidget",
    "SurvivalDecayWidget",
    "TransitionGraphWidget",
]

