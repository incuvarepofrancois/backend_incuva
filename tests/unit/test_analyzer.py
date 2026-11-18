import pytest
from unittest.mock import patch


def test_analyzer_empty_transcript():
    from app.modules.meeting_intelligence.analyzer import MeetingAnalyzer
    analyzer = MeetingAnalyzer()
    result = analyzer.analyze("")
    assert result == {"decisions": [], "action_items": [], "participants": []}


def test_analyzer_with_transcript():
    from app.modules.meeting_intelligence.analyzer import MeetingAnalyzer
    analyzer = MeetingAnalyzer()
    response = '{"decisions":["Prioriser le backend"],"action_items":[{"task":"Préparer le plan","owner":"Alice","due_date":"2026-11-01"}],"participants":["Alice"]}'
    with patch.object(analyzer.llm, "call", return_value=response):
        result = analyzer.analyze("Alice: I am thinking about leaving.")
    assert result["decisions"] == ["Prioriser le backend"]
    assert result["action_items"][0]["owner"] == "Alice"
    assert result["participants"] == ["Alice"]


def test_analyzer_parses_fenced_json_and_rejects_invalid_output():
    from app.modules.meeting_intelligence.analyzer import MeetingAnalyzer
    analyzer = MeetingAnalyzer()
    assert analyzer._parse_response('```json\n{"decisions":[],"action_items":[],"participants":[]}\n```')["decisions"] == []
    with pytest.raises(ValueError):
        analyzer._parse_response("not json")


def test_analyzer_rejects_malformed_action_items():
    from app.modules.meeting_intelligence.analyzer import MeetingAnalyzer
    with pytest.raises(ValueError):
        MeetingAnalyzer._parse_response('{"action_items":[{"owner":"Alice"}]}')


def test_extractor_empty():
    from app.modules.meeting_intelligence.extractor import DecisionExtractor
    extractor = DecisionExtractor()
    result = extractor.extract("")
    assert result["decisions"] == []
    assert result["action_items"] == []
    assert result["participants"] == []


def test_signal_collector_aggregate():
    from app import create_app, db
    from app.modules.risk_detection.signals import SignalCollector
    collector = SignalCollector()
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        result = collector.aggregate(99)
        assert isinstance(result, list)
        db.session.remove()
        db.drop_all()
