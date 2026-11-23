"""Dependency-free tests for the publication staleness guard.

The production module imports FastAPI only for HTTPException; the decision logic
itself is plain Python, so a tiny stub lets these tests run without the full
stack or a PostgreSQL server. Run: python3 backend/tests/test_publication_guard.py
"""
import sys
import types
import unittest
from datetime import date

# --- Stub the one third-party import used by publication_guard ----------------
fastapi = types.ModuleType("fastapi")


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


fastapi.HTTPException = HTTPException
sys.modules.setdefault("fastapi", fastapi)
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from publication_guard import evaluate, ensure_publishable  # noqa: E402


class Cursor:
    """Minimal cursor supporting execute(...).fetchall()."""

    def __init__(self, walks):
        self.walks = walks

    def execute(self, query, params):
        self.locked = "FOR UPDATE" in query
        lo, hi = params
        self._result = sorted(
            (dict(w) for w in self.walks if lo <= w["walked_on"] <= hi),
            key=lambda w: (w["walked_on"], w["id"]),
        )
        return self

    def fetchall(self):
        return self._result


def walk(wid, status, day=23):
    return {
        "id": wid, "route_code": "WX", "walked_on": date(2026, 11, day),
        "crew": f"组{wid}", "status": status,
    }


def snapshot(included, excluded, unreviewed=()):
    return {
        "id": 1, "date_from": date(2026, 11, 16), "date_to": date(2026, 11, 23),
        "result": {
            "included_walks": list(included),
            "excluded_walks": [{"id": i, "reason": "x"} for i in excluded],
            "unreviewed_walks": list(unreviewed),
        },
    }


class StalenessTests(unittest.TestCase):
    def test_clean_snapshot_is_ready_and_locks_rows(self):
        cx = Cursor([walk(1, "valid"), walk(2, "excluded")])
        report = evaluate(cx, snapshot([1], [2]))
        self.assertTrue(report["ready"])
        self.assertEqual(report["stale_walks"], [])
        self.assertEqual(report["pending_walks"], [])
        self.assertTrue(cx.locked, "publish path must lock in-scope walks FOR UPDATE")

    def test_readiness_display_does_not_lock(self):
        cx = Cursor([walk(1, "valid")])
        evaluate(cx, snapshot([1], []), lock=False)
        self.assertFalse(cx.locked, "the display endpoint must not block reviews")

    def test_late_review_moving_returned_to_valid_is_stale(self):
        # Frozen while walk 3 was still pending; review has now completed.
        cx = Cursor([walk(1, "valid"), walk(3, "valid")])
        report = evaluate(cx, snapshot([1], [], unreviewed=[3]))
        self.assertFalse(report["ready"])
        self.assertEqual([w["walk_id"] for w in report["stale_walks"]], [3])
        self.assertIn("重编", report["stale_walks"][0]["change"])

    def test_late_review_to_excluded_is_stale(self):
        cx = Cursor([walk(1, "valid"), walk(3, "excluded")])
        report = evaluate(cx, snapshot([1], [], unreviewed=[3]))
        self.assertEqual([w["walk_id"] for w in report["stale_walks"]], [3])

    def test_review_flipping_included_walk_to_excluded_is_stale(self):
        cx = Cursor([walk(1, "valid"), walk(2, "excluded")])
        report = evaluate(cx, snapshot([1, 2], []))  # 2 was counted at freeze
        self.assertEqual([w["walk_id"] for w in report["stale_walks"]], [2])

    def test_review_flipping_excluded_walk_to_valid_is_stale(self):
        cx = Cursor([walk(1, "valid"), walk(2, "valid")])
        report = evaluate(cx, snapshot([1], [2]))  # 2 was excluded at freeze
        self.assertEqual([w["walk_id"] for w in report["stale_walks"]], [2])

    def test_unreviewed_supplementary_booking_blocks(self):
        cx = Cursor([walk(1, "valid"), walk(4, "returned")])
        report = evaluate(cx, snapshot([1], []))
        self.assertEqual([w["walk_id"] for w in report["pending_walks"]], [4])
        self.assertEqual(report["stale_walks"], [])
        self.assertFalse(report["ready"])

    def test_booked_and_walking_are_pending(self):
        cx = Cursor([walk(1, "valid"), walk(5, "booked"), walk(6, "walking")])
        report = evaluate(cx, snapshot([1], []))
        self.assertEqual(
            sorted(w["walk_id"] for w in report["pending_walks"]), [5, 6])

    def test_out_of_scope_supplement_does_not_block(self):
        # Walk 9 sits outside the snapshot window; it must be invisible.
        cx = Cursor([walk(1, "valid"), walk(9, "returned", day=25)])
        report = evaluate(cx, snapshot([1], []))
        self.assertTrue(report["ready"])
        self.assertEqual(report["pending_walks"], [])
        self.assertEqual(report["stale_walks"], [])

    def test_no_live_effort_is_not_ready_even_without_changes(self):
        cx = Cursor([walk(7, "excluded")])
        report = evaluate(cx, snapshot([], [7]))
        self.assertFalse(report["ready"])
        self.assertFalse(report["has_live_effort"])

    def test_ensure_publishable_raises_with_named_shifts(self):
        cx = Cursor([walk(1, "valid"), walk(3, "valid")])
        with self.assertRaises(HTTPException) as ctx:
            ensure_publishable(cx, snapshot([1], [], unreviewed=[3]))
        self.assertEqual(ctx.exception.status_code, 409)
        body = ctx.exception.detail
        self.assertIn("P-003", body["message"])
        self.assertEqual(body["snapshot_id"], 1)
        self.assertEqual(body["stale_walks"][0]["reference"], "P-003")

    def test_ensure_publishable_passes_for_fresh_pivot(self):
        cx = Cursor([walk(1, "valid"), walk(2, "excluded")])
        report = ensure_publishable(cx, snapshot([1], [2]))
        self.assertTrue(report["ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
