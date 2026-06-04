"""P3 — bias point-in-time gate (spec §13, constraint C1).

The **higher-level** half of the leak-proof bias seam. The mechanical half lives
in Rust (``validate_bias`` in ``portfolio_engine.rs``, P1.6): default-forbidden,
``available_at[t] <= decision_time[t]``, per-``t`` availability mandatory, finite,
shape. This module builds the typed artifact and the provenance / fold-locality /
embargo / window-constant checks the Rust seam *cannot* see, then composes with
the Rust seam by calling the real ``run_panel(bias=..., bias_mode=...)`` — so a
caller bypassing this module still cannot smuggle a future-stamped bias.

Why a Python layer at all (the gap the Rust `<=` check leaves open): a thesis
formed *today* and stamped backward across a historical fold with a single
``generated_at`` timestamp passes ``available_at[t] <= decision_time[t]`` for every
``t`` (the stamp predates the bars) yet is exactly the §13 leak — "overweight semis,
derived Jun-2026, applied to a Jan–Jun-2026 fold". A window-constant availability
is undetectable from the values alone; it is caught here.

Fold-locality is **mechanical, not a self-report** (§13 L393-394, L398). The
manifest's ``train_start`` / ``train_end`` are caller-supplied and therefore
untrusted — a caller could paste fold-local bounds onto a manifest carrying
hindsight values. So for ``mode=="backtest"`` the gate **resolves**
``source.run_id`` against a :class:`BiasRunRegistry` of *recorded* production
provenance and uses the **registered** train window (never the artifact's
self-reported bounds) for the fold-locality subset check:

  * a ``run_id`` not in the registry → rejected (fabricated / unresolved id);
  * a resolved ``run_id`` whose **registered** window is not a subset of the
    fold's train window → rejected (lying self-reported bounds cannot help);
  * an optional values fingerprint mismatch vs. the registered one → rejected
    (tamper check).

A legitimate bias producer calls :meth:`BiasRunRegistry.register` when it
generates bias from a known train window; the registry starts EMPTY.
``mode=="forward"`` bias is injected live going forward and never touches a
historical fold (§13 rule 2), so fold-locality / registry resolution do not
apply — only the PIT / forbidden / finite checks run.

OUT of scope: this is the bias *gate*, not the thesis-lane bias *producer*
(§13 forward-only lane). No DART/news/agent code lives here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from bt.bt_engine import run_panel
from bt.panel.loader import Panel
from bt.panel.walkforward import Fold


class BiasRejected(Exception):
    """Raised when a bias artifact fails the point-in-time / provenance gate."""


def fingerprint_values(bias_values: np.ndarray) -> str:
    """SHA-256 of the canonicalized bias_values bytes (for tamper detection).

    Canonicalizes to C-contiguous float64 so the same logical matrix always
    hashes identically regardless of incoming dtype / memory layout.
    """
    arr = np.ascontiguousarray(np.asarray(bias_values, dtype=np.float64))
    return hashlib.sha256(arr.tobytes()).hexdigest()


@dataclass(frozen=True)
class BiasRunRecord:
    """Recorded ACTUAL provenance of one bias production run.

    ``train_start`` / ``train_end`` are the UTC-ms bounds of the data the bias was
    *actually* produced from (recorded by the producer, not self-reported by the
    consumed artifact). ``fingerprint`` is the optional SHA-256 of the produced
    ``bias_values`` for tamper detection.
    """

    train_start: int
    train_end: int
    generated_at: int
    fingerprint: Optional[str] = None


class BiasRunRegistry:
    """In-process registry mapping ``run_id`` -> recorded :class:`BiasRunRecord`.

    The fold-locality gate resolves a backtest artifact's ``source.run_id`` here
    and uses the **registered** train window (not the artifact's self-reported
    bounds) for the subset check. Starts EMPTY: an unregistered ``run_id`` is a
    fabricated / unresolved id and is rejected. A module-level default instance
    backs the production gate; tests inject a fresh instance to avoid shared
    mutable state across the session.
    """

    def __init__(self) -> None:
        self._runs: dict[str, BiasRunRecord] = {}

    def register(
        self,
        run_id: str,
        train_start: int,
        train_end: int,
        *,
        generated_at: int,
        fingerprint: Optional[str] = None,
    ) -> BiasRunRecord:
        """Record the ACTUAL provenance of a bias production run."""
        if not run_id:
            raise ValueError("run_id must be a non-empty string")
        rec = BiasRunRecord(
            train_start=int(train_start),
            train_end=int(train_end),
            generated_at=int(generated_at),
            fingerprint=fingerprint,
        )
        self._runs[run_id] = rec
        return rec

    def resolve(self, run_id: str) -> Optional[BiasRunRecord]:
        """Return the recorded provenance for ``run_id``, or ``None`` if absent."""
        return self._runs.get(run_id)


# Module-level default registry backing the production gate (starts EMPTY).
_DEFAULT_REGISTRY = BiasRunRegistry()


def default_registry() -> BiasRunRegistry:
    """Return the module-level default :class:`BiasRunRegistry`."""
    return _DEFAULT_REGISTRY


@dataclass
class BiasSourceManifest:
    """Provenance of a bias artifact.

    ``train_start`` / ``train_end`` are the UTC-ms bounds the consumer *claims* the
    bias was produced from. They are **self-reported and untrusted**: for a
    backtest the gate resolves ``run_id`` against a :class:`BiasRunRegistry` and
    uses the *registered* window for fold-locality, never these fields.
    ``values_fingerprint`` is an optional SHA-256 of ``bias_values`` checked
    against the registry's recorded fingerprint (tamper detection).
    """

    run_id: str
    train_start: int
    train_end: int
    generated_at: int
    values_fingerprint: Optional[str] = field(default=None)


@dataclass
class BiasArtifact:
    """Typed bias artifact (spec §13 rule 1).

    ``bias_values`` ``[T,N]`` exogenous tilt; ``available_at`` ``[T]`` the per-bar
    timestamp the value became knowable; ``source`` provenance manifest; ``mode``
    one of ``{"backtest","forward"}`` (``None``/unset → forbidden by default).
    """

    bias_values: np.ndarray
    available_at: Optional[np.ndarray]
    source: BiasSourceManifest
    mode: Optional[str]


def validate_bias_artifact(
    artifact: BiasArtifact,
    *,
    panel: Panel,
    fold: Fold,
    embargo_bars: int = 0,
    registry: Optional[BiasRunRegistry] = None,
) -> BiasArtifact:
    """Validate a bias artifact against a fold's point-in-time + provenance rules.

    Raises :class:`BiasRejected` on any violation; returns the artifact on pass.
    The Rust seam re-checks the mechanical inequalities at ``run_panel`` ingestion
    (see :func:`apply_bias`) — this gate adds the checks Rust cannot do:

      1. **Typed artifact** — a raw ndarray (no :class:`BiasArtifact`) is rejected.
      2. **Default-forbidden** — ``mode`` unset/``None`` → reject (a backtest has
         no "current view"). Mode must be ``"backtest"`` or ``"forward"``.
      3. **Per-``t`` availability mandatory** — missing ``available_at`` or length
         ``!= T`` (scalar / window-constant by length) → reject.
      4. **Window-constant availability** — an all-identical ``available_at`` is the
         "today's view stamped backward" smuggle; reject even though every entry
         is ``<= decision_time``.
      5. **Future availability** — ``available_at[t] > timestamp[t]`` → reject.
      6. **Fold-locality (backtest only, mechanical)** — resolve ``source.run_id``
         against ``registry`` (default: the module registry). An unregistered id is
         a fabricated / unresolved run and is rejected; a resolved run's
         **registered** train window (never the artifact's self-reported bounds)
         must be a subset of this fold's train window. An optional values
         fingerprint mismatch vs. the registry's recorded one is rejected (tamper).
         For ``mode=="forward"`` this step is skipped — forward bias is injected
         live going forward and never touches a historical fold (§13 rule 2).
      7. **Purge/embargo** — every bias availability used in the fold's *test*
         segment must precede the embargoed train→test boundary by ``embargo_bars``.

    Args:
        registry: the :class:`BiasRunRegistry` resolved against for backtest
            fold-locality. ``None`` → the module-level default registry.
    """
    if registry is None:
        registry = _DEFAULT_REGISTRY
    # 1. typed artifact
    if not isinstance(artifact, BiasArtifact):
        raise BiasRejected(
            "bias must be a typed BiasArtifact (raw arrays rejected) — "
            "provenance/availability cannot be verified from values alone"
        )

    # 2. default-forbidden mode
    if artifact.mode not in ("backtest", "forward"):
        raise BiasRejected(
            f"bias mode must be 'backtest' or 'forward', got {artifact.mode!r} "
            "(default forbidden — a backtest has no 'current view')"
        )

    T = int(panel.timestamp.size)
    N = len(panel.assets)
    bv = np.asarray(artifact.bias_values, dtype=np.float64)
    if bv.shape != (T, N):
        raise BiasRejected(f"bias_values shape {bv.shape} != (T={T}, N={N})")
    if not np.all(np.isfinite(bv)):
        raise BiasRejected("bias_values must be finite")

    # 3. per-t availability mandatory
    if artifact.available_at is None:
        raise BiasRejected("bias requires per-t available_at (none provided)")
    av = np.asarray(artifact.available_at, dtype=np.int64)
    if av.size != T:
        raise BiasRejected(
            f"available_at length {av.size} != T={T} "
            "(scalar / window-constant bias rejected)"
        )

    # 4. window-constant availability (the §13 smuggle Rust cannot see)
    if T > 1 and np.unique(av).size == 1:
        raise BiasRejected(
            "available_at is window-constant (all identical) — 'today's view "
            "stamped backward across the fold' is the §13 leak, rejected"
        )

    # 5. future availability (Rust re-checks at ingestion; we fail early w/ ctx)
    dt = panel.timestamp.astype(np.int64)
    bad = np.where(av > dt)[0]
    if bad.size:
        i = int(bad[0])
        raise BiasRejected(
            f"future bias: available_at[{i}]={av[i]} > decision_time[{i}]={dt[i]}"
        )

    # 6. fold-locality (backtest only): RESOLVE source.run_id against the registry
    # and use the REGISTERED train window (the artifact's self-reported bounds are
    # untrusted) for the subset check. forward bias is injected live and never
    # touches a historical fold (§13 rule 2), so this step is skipped for it.
    src = artifact.source
    if artifact.mode == "backtest":
        record = registry.resolve(src.run_id)
        if record is None:
            raise BiasRejected(
                f"unresolved/fabricated run_id: {src.run_id!r} — fold-locality is "
                "mechanical (§13 C1); a backtest bias must resolve to a recorded "
                "production manifest in the registry, not a self-reported window"
            )

        # tamper check: if both sides carry a fingerprint, they must match
        if src.values_fingerprint is not None and record.fingerprint is not None:
            actual_fp = fingerprint_values(artifact.bias_values)
            if (
                src.values_fingerprint != record.fingerprint
                or actual_fp != record.fingerprint
            ):
                raise BiasRejected(
                    f"bias_values fingerprint mismatch for run_id {src.run_id!r} "
                    "— artifact tampered or does not match the registered run"
                )

        train_idx = np.asarray(fold.train_idx, dtype=np.int64)
        if train_idx.size == 0:
            raise BiasRejected("fold has empty train window — cannot localize bias")
        fold_train_start = int(panel.timestamp[train_idx[0]])
        fold_train_end = int(panel.timestamp[train_idx[-1]])
        if not (
            record.train_start >= fold_train_start
            and record.train_end <= fold_train_end
        ):
            raise BiasRejected(
                f"non-fold-local source '{src.run_id}': REGISTERED train window "
                f"[{record.train_start},{record.train_end}] is not a subset of fold "
                f"train window [{fold_train_start},{fold_train_end}] — bias must be "
                "produced from this fold's train data only (self-reported bounds "
                f"[{src.train_start},{src.train_end}] are ignored)"
            )

    # 7. purge/embargo: bias availability for test bars must sit before the
    # embargoed boundary. The embargoed boundary is the train-end timestamp
    # pushed forward by embargo_bars rows (in panel-row terms). This is a
    # fold-relative check; forward bias has no historical fold (§13 rule 2).
    if artifact.mode == "backtest" and embargo_bars > 0:
        test_idx = np.asarray(fold.test_idx, dtype=np.int64)
        # the embargo boundary timestamp = generated_at + embargo span; equiv.,
        # the bias for a test bar must have been available no later than the bar
        # `embargo_bars` rows BEFORE that test bar (a feature passing embargo).
        for ti in test_idx:
            boundary_row = int(ti) - embargo_bars
            if boundary_row < 0:
                raise BiasRejected(
                    f"test bar {int(ti)} cannot satisfy embargo of {embargo_bars} "
                    "bars (not enough lead) — bias fails purge/embargo"
                )
            boundary_ts = int(panel.timestamp[boundary_row])
            if int(av[int(ti)]) > boundary_ts:
                raise BiasRejected(
                    f"bias for test bar {int(ti)} available at {int(av[int(ti)])} "
                    f"violates {embargo_bars}-bar embargo (must be <= {boundary_ts})"
                )

    return artifact


def apply_bias(
    panel: Panel,
    weights: np.ndarray,
    artifact: BiasArtifact,
    *,
    run_cfg: dict,
    fold: Fold,
    embargo_bars: int = 0,
    registry: Optional[BiasRunRegistry] = None,
) -> dict:
    """Validate ``artifact`` then run ``run_panel`` with the bias tilt.

    Composes both layers: this module's provenance/fold-locality/embargo gate
    (:func:`validate_bias_artifact`) **and** the Rust ingestion seam (re-checks
    the mechanical inequalities). Returns the ``run_panel`` result dict.
    ``registry`` is forwarded to the gate for backtest fold-locality resolution.
    """
    validate_bias_artifact(
        artifact, panel=panel, fold=fold, embargo_bars=embargo_bars, registry=registry
    )

    T, N = panel.close.shape
    return run_panel(
        panel.assets,
        np.ascontiguousarray(panel.close, dtype=np.float64),
        np.ascontiguousarray(panel.open, dtype=np.float64),
        np.ascontiguousarray(panel.funding, dtype=np.float64),
        np.ascontiguousarray(panel.fx, dtype=np.float64),
        np.ascontiguousarray(weights, dtype=np.float64),
        panel.active_mask.astype(np.float64),
        panel.session.astype(np.int64),
        panel.timestamp.astype(np.int64),
        run_cfg,
        np.ascontiguousarray(artifact.bias_values, dtype=np.float64),
        np.asarray(artifact.available_at, dtype=np.int64),
        artifact.mode,
    )
