class Recommender:
    def get_recommendation(self, risk_score, drivers):
        if risk_score >= 75:
            return "1:1_urgent"
        if risk_score >= 50:
            return "feedback_session"
        if risk_score >= 25:
            return "monitor"
        return "no_action"
