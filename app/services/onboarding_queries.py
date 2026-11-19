"""Organization-scoped onboarding reads shared by REST and internal capabilities."""

from sqlalchemy import desc

from app.models.onboarding import OnboardingPlan


ONBOARDING_PLAN_LIMIT = 200


def list_onboarding_plans(org_id: int) -> list[OnboardingPlan]:
    """Return the same most-recent-first, capped plan set as the existing API."""
    return (
        OnboardingPlan.query.filter_by(org_id=org_id)
        .order_by(desc(OnboardingPlan.updated_at), desc(OnboardingPlan.id))
        .limit(ONBOARDING_PLAN_LIMIT)
        .all()
    )
