"""Balance contributions between saved views, not invented financial causes."""

from datetime import datetime

from sqlalchemy import select

from mypocket.saved_view.policy import SavedViewUnavailable
from mypocket.saved_view.schema import account_scopes, balances, runs
from mypocket.saved_view.service import snapshot, stamp


def compare(previous, current):
    """Accept only the service's minimal projection; never raw provider records."""
    for key in ("kind", "version", "owner_scope", "generation", "field_set", "currency"):
        if previous[key] != current[key]:
            raise SavedViewUnavailable("incomparable_snapshots")
    if datetime.fromisoformat(current["captured_at"]) <= datetime.fromisoformat(previous["captured_at"]):
        raise SavedViewUnavailable("non_increasing_capture_time")
    before = {a["ref"]: a for a in previous["accounts"]}
    after = {a["ref"]: a for a in current["accounts"]}
    changes, total = [], 0
    complete = True
    for ref in sorted(before.keys() | after.keys()):
        old, new = before.get(ref), after.get(ref)
        account = new or old
        if old and new and old["type"] != new["type"]:
            raise SavedViewUnavailable("incomparable_snapshots")
        old_amount = old["balance_minor"] if old else 0
        new_amount = new["balance_minor"] if new else 0
        known = old_amount is not None and new_amount is not None
        contribution = (new_amount - old_amount) * (-1 if account["type"] == "credit" else 1) if known else None
        if known:
            total += contribution
        else:
            complete = False
        changes.append({"ref": ref, "label": account["label"],
                        "kind": "added_to_view" if not old else "removed_from_view" if not new else "balance_change",
                        "contribution_minor": contribution,
                        "cause": "not_determined"})
    return {"from_captured_at": previous["captured_at"], "to_captured_at": current["captured_at"],
            "complete": complete, "net_change_minor": total if complete else None,
            "contributions": changes,
            "explanation": "Account balance differences only; deposits, spending, transfers and market movements are not attributed."}


def recorded_changes(session, *, now=None):
    """Explain only two completed balance observations for each approved account.

    Different accounts can have different observation windows, so no combined
    total or daily-return label is invented.
    """
    current = snapshot(session, now=now)  # Performs the same consent and owner gate.
    selected = dict(session.execute(select(account_scopes.c.public_ref, account_scopes.c.account_id)).all())
    result = []
    for account in current["accounts"]:
        item = {"ref": account["ref"], "label": account["label"], "status": "insufficient_history",
                "previous_observed_at": None, "current_observed_at": account["observed_at"],
                "balance_change_minor": None, "net_worth_contribution_minor": None, "cause": "not_determined"}
        points = session.execute(select(balances).join(runs, balances.c.run_id == runs.c.id)
                                 .where(balances.c.account_id == selected[account["ref"]], runs.c.outcome == "success",
                                        runs.c.finished_at <= datetime.fromisoformat(current["captured_at"]))
                                 .order_by(balances.c.observed_at.desc(), runs.c.id.desc()).limit(2)).mappings().all()
        if account["freshness"] == "unverified":
            item["status"] = "current_value_unverified"
        elif len(points) == 2:
            new, old = points
            compatible = (stamp(new["observed_at"]) == account["observed_at"]
                          and new["balance_minor"] == account["balance_minor"]
                          and all(p["currency"] == current["currency"] and p["account_type"] == account["type"]
                                  and p["balance_minor"] is not None for p in points))
            if compatible and old["observed_at"] < new["observed_at"]:
                change = new["balance_minor"] - old["balance_minor"]
                item.update(status="compared", previous_observed_at=stamp(old["observed_at"]),
                            balance_change_minor=change,
                            net_worth_contribution_minor=change * (-1 if account["type"] == "credit" else 1))
        result.append(item)
    return {"captured_at": current["captured_at"], "currency": current["currency"], "accounts": result,
            "owner_scope": current["owner_scope"], "generation": current["generation"],
            "explanation": "Each account uses its own two completed checks. Balance changes do not identify spending, deposits or investment returns."}
