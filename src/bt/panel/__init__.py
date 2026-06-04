"""Multi-asset panel layer (P0+): loader, session tagger, strategy, engine wiring."""

from __future__ import annotations

from bt.panel.bias import (
    BiasArtifact,
    BiasRejected,
    BiasRunRecord,
    BiasRunRegistry,
    BiasSourceManifest,
    apply_bias,
    default_registry,
    fingerprint_values,
    validate_bias_artifact,
)
from bt.panel.loader import Panel, PanelSchemaError, load_panel
from bt.panel.primitives import CrossSectionalStrategy, PairsStrategy
from bt.panel.run import detect_lookahead, run_panel_strategy
from bt.panel.session import tag_session
from bt.panel.stats import (
    bh_fdr,
    deflated_sharpe_ratio,
    pbo,
    probabilistic_sharpe_ratio,
)
from bt.panel.strategy import (
    LookaheadError,
    PanelStrategy,
    PanelView,
    WeightMatrix,
)
from bt.panel.walkforward import Fold, make_folds, walk_forward

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
    "Fold",
    "make_folds",
    "walk_forward",
    "bh_fdr",
    "deflated_sharpe_ratio",
    "pbo",
    "probabilistic_sharpe_ratio",
    "BiasArtifact",
    "BiasRejected",
    "BiasRunRecord",
    "BiasRunRegistry",
    "BiasSourceManifest",
    "apply_bias",
    "default_registry",
    "fingerprint_values",
    "validate_bias_artifact",
]
