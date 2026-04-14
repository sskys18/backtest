"""Metrics formatting and display helpers."""

from __future__ import annotations


def format_metrics(metrics: dict) -> str:
    """Format metrics as a human-readable string."""
    lines = [
        f"Run: {metrics.get('run_id', 'N/A')}",
        f"Total Return:    {metrics.get('total_return', 0):.2%}",
        f"Sharpe Ratio:    {metrics.get('sharpe_ratio', 0):.4f}",
        f"Sortino Ratio:   {metrics.get('sortino_ratio', 0):.4f}",
        f"Max Drawdown:    {metrics.get('max_drawdown', 0):.2%}",
        f"Round Trips:     {metrics.get('num_round_trips', 0)}",
        f"Win Rate:        {metrics.get('win_rate', 0):.2%}",
        f"Profit Factor:   {metrics.get('profit_factor', 0):.4f}",
    ]
    return "\n".join(lines)
