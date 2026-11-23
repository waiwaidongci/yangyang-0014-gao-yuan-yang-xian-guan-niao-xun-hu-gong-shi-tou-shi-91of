"""Detect frozen pivots that no longer match the patrol record they summarize.

A snapshot freezes the in-scope walks at one moment. Late supplementary booking
or a review that lands afterwards changes the live record, so publishing the
old pivot would silently drop or mis-state a shift. Recomputing a fresh snapshot
is the only way to bring those changes into a signed edition.
"""
from fastapi import HTTPException

# Statuses that have not yet passed review; such in-scope walks block signing.
UNREVIEWED = ("booked", "walking", "returned")

CHANGE_LABELS = {
    "booked": "补登后待出发",
    "walking": "补登后巡护中",
    "returned": "补登或复核变化后待复核",
    "valid": "复核后改为计入工时，需重编",
    "excluded": "复核后改为剔除，需重编",
}


def walk_ref(walk_id):
    return f"P-{int(walk_id):03d}"


def _classify(current, frozen_ids):
    """Return per-walk change wording from a live row and the frozen scope."""
    status = current["status"]
    if status in UNREVIEWED:
        return "pending", CHANGE_LABELS[status]
    in_included = current["id"] in frozen_ids["included"]
    in_excluded = current["id"] in frozen_ids["excluded"]
    if not in_included and not in_excluded:
        if status == "valid":
            return "stale", "晚到复核后计入统计，需重编"
        return "stale", "晚到复核后剔除，需重编"
    if in_included and status == "excluded":
        return "stale", CHANGE_LABELS["excluded"]
    if in_excluded and status == "valid":
        return "stale", CHANGE_LABELS["valid"]
    if in_included:
        return "stale", "已计入班次的复核口径变化，需重编"
    return "stale", "已剔除班次的复核口径变化，需重编"


def evaluate(cx, snapshot, lock=True):
    """Compare a frozen snapshot against the current in-scope patrol walks.

    With lock=True the in-scope rows are taken FOR UPDATE, so a review racing
    signing either commits before this read (and is therefore reflected) or
    waits until signing finishes; only reviews effective before signing takes
    effect count. Display callers pass lock=False to avoid blocking reviewers.
    Walks outside the snapshot date window never participate, so out-of-scope
    supplementary booking cannot block an edition.
    """
    frozen = snapshot["result"] or {}
    frozen_ids = {
        "included": set(frozen.get("included_walks", [])),
        "excluded": {item["id"] for item in frozen.get("excluded_walks", [])},
    }
    walks = cx.execute(
        f"""SELECT id,route_code,walked_on,crew,status FROM patrol_walk
           WHERE walked_on BETWEEN %s AND %s
           ORDER BY walked_on,id {'FOR UPDATE' if lock else ''}""",
        (snapshot["date_from"], snapshot["date_to"]),
    ).fetchall()

    stale, pending = [], []
    for walk in walks:
        if walk["id"] in frozen_ids["included"] and walk["status"] == "valid":
            continue  # still counted exactly as frozen
        if walk["id"] in frozen_ids["excluded"] and walk["status"] == "excluded":
            continue  # still excluded exactly as frozen
        kind, detail = _classify(walk, frozen_ids)
        item = {
            "walk_id": walk["id"],
            "reference": walk_ref(walk["id"]),
            "route_code": walk["route_code"],
            "walked_on": walk["walked_on"].isoformat(),
            "crew": walk["crew"],
            "status": walk["status"],
            "change": detail,
        }
        (pending if kind == "pending" else stale).append(item)

    live_valid = [walk for walk in walks if walk["status"] == "valid"]
    return {
        "snapshot_id": snapshot["id"],
        "ready": not stale and not pending and bool(live_valid),
        "stale_walks": stale,
        "pending_walks": pending,
        "has_live_effort": bool(live_valid),
    }


def ensure_publishable(cx, snapshot):
    """Block signing of an outdated pivot and name every shift that changed."""
    report = evaluate(cx, snapshot)
    if report["pending_walks"] or report["stale_walks"]:
        blocked = report["pending_walks"] + report["stale_walks"]
        names = "、".join(f"{item['reference']}（{item['change']}）" for item in blocked)
        if report["pending_walks"]:
            message = f"范围内尚有班次未完成复核，且旧透视可能漏算：{names}。请先复核并重编透视后再签发。"
        else:
            message = f"该冻结透视已被补登或复核变化追过，需重编后再签发：{names}。"
        raise HTTPException(409, detail={"message": message, **report})
    if not report["has_live_effort"]:
        raise HTTPException(409, "发布前需有有效工时并复核统计范围内全部班次")
    return report
