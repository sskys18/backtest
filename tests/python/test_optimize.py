"""Test optimizer implementations."""

import pytest


def _mock_objective(params: dict) -> float:
    """Simple quadratic: maximize -(x-3)^2 + 10. Peak at x=3, score=10."""
    x = params.get("x", 0)
    return -(x - 3) ** 2 + 10


class TestGridSearch:
    def test_finds_best(self):
        from bt.optimize.grid import GridSearchOptimizer

        opt = GridSearchOptimizer()
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0, "max": 6, "step": 1}},
            budget=10,
        )
        assert result["best_config"]["x"] == 3
        assert result["best_score"] == pytest.approx(10.0)
        assert result["method"] == "grid"

    def test_respects_budget(self):
        from bt.optimize.grid import GridSearchOptimizer

        opt = GridSearchOptimizer()
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0, "max": 100, "step": 1}},
            budget=5,
        )
        assert result["evaluated"] <= 5


class TestRandomSearch:
    def test_returns_result(self):
        from bt.optimize.grid import RandomSearchOptimizer

        opt = RandomSearchOptimizer()
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0.0, "max": 6.0, "step": 0.1}},
            budget=20,
        )
        assert result["best_score"] is not None
        assert result["method"] == "random"
        assert result["evaluated"] == 20


class TestBayesianOptimizer:
    def test_finds_near_optimum(self):
        from bt.optimize.bayesian import BayesianOptimizer

        opt = BayesianOptimizer(seed=42)
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0.0, "max": 6.0}},
            budget=30,
        )
        # Should get close to x=3, score=10 within 30 trials
        assert result["best_score"] >= 9.0
        assert result["method"] == "bayesian"

    def test_with_integer_params(self):
        from bt.optimize.bayesian import BayesianOptimizer

        opt = BayesianOptimizer(seed=42)
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0, "max": 6, "step": 1}},
            budget=10,
        )
        assert isinstance(result["best_config"]["x"], int)

    def test_with_categorical_params(self):
        from bt.optimize.bayesian import BayesianOptimizer

        opt = BayesianOptimizer(seed=42)
        result = opt.optimize(
            _mock_objective,
            {"x": {"values": [1, 2, 3, 4, 5]}},
            budget=10,
        )
        assert result["best_config"]["x"] in [1, 2, 3, 4, 5]
        assert result["best_config"]["x"] == 3
