def format_datetime(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def format_risk_score(score):
    return {
        "value": round(score, 2),
        "percentage": f"{round(score, 1)}%",
        "level": _risk_level(score),
    }


def _risk_level(score):
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def paginate_response(items, total, page, per_page):
    return {
        "data": items,
        "pagination": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        },
    }
