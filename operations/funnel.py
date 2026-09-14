"""Recorded cohort denominators include failed research and non-purchases."""


def summarize_funnel(cases):
    types = {identity:set(case.get("event_types",())) for identity,case in cases.items()}
    contributions = [case.get("realized_profit_cents") for case in cases.values()]
    return {
        "included_leads":len(cases),
        "unavailable_leads":sum("unavailable" in value for value in types.values()),
        "failed_contacts":sum("contact_failed" in value for value in types.values()),
        "declined_leads":sum("human_declined" in value for value in types.values()),
        "acquired_cases":sum("acquired" in value for value in types.values()),
        "delivered_cases":sum("delivered" in value for value in types.values()),
        "cases_with_returns":sum(bool(value & {"return_opened","returned","refunded"}) for value in types.values()),
        "closed_followup_cases":sum(case.get("followup_status") == "closed" for case in cases.values()),
        "open_or_unfinished_cases":sum(case.get("followup_status") != "closed" for case in cases.values()),
        "known_operator_minutes":sum(case.get("known_operator_minutes",0) for case in cases.values()),
        "unknown_time_events":sum(case.get("unknown_time_events",0) for case in cases.values()),
        "known_cleared_cash_delta_cents":sum(case.get("known_cleared_cash_delta_cents",0) for case in cases.values()),
        "unknown_cash_events":sum(case.get("unknown_cash_events",0) for case in cases.values()),
        "realized_contribution_cents":sum(contributions) if contributions and all(value is not None for value in contributions) else None,
        "known_realized_contribution_cents":sum(value for value in contributions if value is not None),
        "unknown_contribution_cases":sum(value is None for value in contributions),
        "denominator_kind":"all_recorded_inclusions_not_successful_purchases",
    }
