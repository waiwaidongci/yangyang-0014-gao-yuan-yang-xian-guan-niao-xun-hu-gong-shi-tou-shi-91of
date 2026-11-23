"""Manage signed station editions, local delivery receipts, and CSV evidence."""
from fastapi import APIRouter,Depends,HTTPException,Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from core import db,require
from edition_schema import Audience,EditionInput
from edition_service import export_matrix,publish,receive,snapshot_for
from publication_guard import evaluate

router = APIRouter(prefix="/api/editions")


@router.get("")
def shelf(user=Depends(require())):
    with db() as cx:
        return {
            "editions":cx.execute(
                """SELECT e.*,s.label,s.row_axis,s.col_axis,s.date_from,s.date_to,s.taxon
                   FROM station_edition e JOIN effort_snapshot s ON s.id=e.snapshot_id ORDER BY e.id DESC"""
            ).fetchall(),
            "inbox":cx.execute("SELECT * FROM edition_inbox ORDER BY edition_id,audience").fetchall(),
        }


@router.get("/readiness")
def readiness(user=Depends(require())):
    """Report, per frozen pivot, the shifts that force a recompute before signing."""
    with db() as cx:
        snapshots = cx.execute(
            """SELECT * FROM effort_snapshot WHERE status='frozen' ORDER BY id DESC"""
        ).fetchall()
        return {"snapshots": [evaluate(cx, item, lock=False) for item in snapshots]}


@router.post("")
def release(data: EditionInput,user=Depends(require("reviewer"))):
    with db() as cx:
        edition, replayed = publish(cx,data,user["email"])
    # A timeout retry of the exact same edition reuses the existing volume
    # (HTTP 200) instead of opening a new one; a first signing answers 201.
    return JSONResponse(
        jsonable_encoder(edition),
        status_code=200 if replayed else 201,
        headers={"X-Idempotent-Replay": "true" if replayed else "false"},
    )


@router.post("/{id}/inbox/{audience}/receive")
def acknowledge(id: int,audience: Audience,user=Depends(require("operator"))):
    with db() as cx:
        return receive(cx,id,audience,user["email"])


@router.get("/{id}/matrix")
def csv_matrix(id: int,user=Depends(require())):
    with db() as cx:
        edition = cx.execute("SELECT * FROM station_edition WHERE id=%s",(id,)).fetchone()
        if not edition:
            raise HTTPException(404,"未找到对照发布版")
        data = export_matrix(snapshot_for(cx,edition["snapshot_id"]))
    return Response(data,media_type="text/csv",headers={"Content-Disposition":
                    f'attachment; filename="{edition["series"]}-v{edition["volume"]}.csv"'})
