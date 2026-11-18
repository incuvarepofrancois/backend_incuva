from app.modules.risk_detection.signals import SignalCollector
from app.modules.risk_detection.recommender import Recommender


class RiskScorer:
    def __init__(self):
        self.collector = SignalCollector()
        self.recommender = Recommender()

    def calculate_score(self, employee_id):
        signals = self.collector.aggregate(employee_id)
        canonical_context = self.collector.collect_canonical_context(employee_id)
        score = self._compute(signals)
        level = self._level(score)
        drivers = self._top_drivers(signals)
        confidence = min(1.0, len(signals) / 10.0)
        recommendation = self.recommender.get_recommendation(score, drivers)

        return {
            "employee_id": employee_id,
            "risk_score": round(score, 2),
            "risk_level": level,
            "data_status": "ready" if signals else "insufficient_data",
            "signals_count": len(signals),
            "canonical_context": canonical_context,
            "confidence": round(confidence, 2),
            "drivers": drivers,
            "recommendation": recommendation,
        }

    def _compute(self, signals):
        if not signals:
            return 0.0
        total_weight = sum(s.get("weight", 1.0) for s in signals)
        weighted_sum = sum(s.get("value", 0) * s.get("weight", 1.0) for s in signals)
        return min(100.0, (weighted_sum / total_weight) if total_weight else 0.0)

    def _level(self, score):
        if score >= 75:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        return "low"

    def _top_drivers(self, signals):
        sorted_signals = sorted(
            signals,
            key=lambda s: s.get("value", 0) * s.get("weight", 1.0),
            reverse=True,
        )
        return [
            {"type": s.get("type"), "value": s.get("value"), "source": s.get("source")}
            for s in sorted_signals[:5]
        ]
