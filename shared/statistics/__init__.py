"""Statistics package.

Re-exports the original single-file `statistics.py` API (unchanged) and
adds Bailey / López de Prado overfitting diagnostics in
`shared.statistics.deflated_sharpe`.
"""
# _validation depends on statsmodels (only installed in backtest/strategy-lab
# images). Guard the import so lighter images (intelligence, execution, etc.)
# can still load shared.statistics.online_dsr / .deflated_sharpe.
try:
    from shared.statistics._validation import (  # noqa: F401
        test_stationarity,
        test_autocorrelation,
        regression_alpha_beta,
        validate_backtest,
    )
except ImportError:
    test_stationarity = test_autocorrelation = None  # type: ignore
    regression_alpha_beta = validate_backtest = None  # type: ignore
from shared.statistics.deflated_sharpe import (  # noqa: F401
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    expected_max_sharpe,
    sharpe_ratio,
    pbo_cscv,
    PBOResult,
)
from shared.statistics.online_dsr import (  # noqa: F401
    OnlineDSR,
    AlphaPauseDecider,
    rolling_dsr_from_history,
)
