"""ML labeling utilities — triple barrier, meta-labels, sample weights.

Triple-barrier implementations are proprietary (López de Prado AFML-style).
A public-only build has this package empty; plugins provide the IP impl.
"""
try:
    from shared.labels.triple_barrier import (
        TripleBarrierLabel,
        triple_barrier_labels,
        daily_vol,
        apply_meta_label,
    )
    __all__ = [
        "TripleBarrierLabel",
        "triple_barrier_labels",
        "daily_vol",
        "apply_meta_label",
    ]
except ImportError:
    TripleBarrierLabel = None  # type: ignore
    triple_barrier_labels = daily_vol = apply_meta_label = None  # type: ignore
    __all__ = []
