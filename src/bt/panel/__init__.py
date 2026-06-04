"""Multi-asset panel layer (P0+): loader, session tagger, strategy, engine wiring."""

from __future__ import annotations

from bt.panel.loader import Panel, PanelSchemaError, load_panel
from bt.panel.primitives import CrossSectionalStrategy, PairsStrategy
from bt.panel.run import detect_lookahead, run_panel_strategy
from bt.panel.session import tag_session
from bt.panel.strategy import (
    LookaheadError,
    PanelStrategy,
    PanelView,
    WeightMatrix,
)

__all__ = [
    "Panel",
    "PanelSchemaError",
    "load_panel",
    "tag_session",
    "PanelStrategy",
    "PanelView",
    "WeightMatrix",
    "LookaheadError",
    "CrossSectionalStrategy",
    "PairsStrategy",
    "run_panel_strategy",
    "detect_lookahead",
]
