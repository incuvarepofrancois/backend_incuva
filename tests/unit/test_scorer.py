import pytest
from unittest.mock import patch


def test_risk_scorer_no_signals():
    from app.modules.risk_detection.scorer import RiskScorer
    scorer = RiskScorer()
    with patch.object(scorer.collector, "aggregate", return_value=[]):
        result = scorer.calculate_score(1)
    assert result["risk_score"] == 0.0
    assert result["risk_level"] == "low"
    assert result["employee_id"] == 1


def test_risk_scorer_critical():
    from app.modules.risk_detection.scorer import RiskScorer
    scorer = RiskScorer()
    signals = [{"type": "absenteeism_increase", "value": 80, "weight": 1.0, "source": "bamboohr"}]
    with patch.object(scorer.collector, "aggregate", return_value=signals):
        result = scorer.calculate_score(2)
    assert result["risk_score"] == 80.0
    assert result["risk_level"] == "critical"


def test_risk_level_thresholds():
    from app.modules.risk_detection.scorer import RiskScorer
    scorer = RiskScorer()
    assert scorer._level(0) == "low"
    assert scorer._level(24) == "low"
    assert scorer._level(25) == "medium"
    assert scorer._level(50) == "high"
    assert scorer._level(75) == "critical"
    assert scorer._level(100) == "critical"


def test_recommender():
    from app.modules.risk_detection.recommender import Recommender
    r = Recommender()
    assert r.get_recommendation(80, []) == "1:1_urgent"
    assert r.get_recommendation(60, []) == "feedback_session"
    assert r.get_recommendation(30, []) == "monitor"
    assert r.get_recommendation(10, []) == "no_action"
