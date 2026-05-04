"""Statistics package.

Re-exports the original single-file `statistics.py` API (unchanged) and
adds Bailey / López de Prado overfitting diagnostics in
`shared.statistics.deflated_sharpe`.
"""
from shared.statistics._validation import (  # noqa: F401
    test_stationarity,
    test_autocorrelation,
    regression_alpha_beta,
    validate_backtest,
)
from shared.statistics.deflated_sharpe import (  # noqa: F401
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    expected_max_sharpe,
    sharpe_ratio,
    pbo_cscv,
    PBOResult,
)
