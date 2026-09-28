from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cache
from app.database import get_db
from app.deps import require_admin
from app.errors import conflict, not_found
from app.models import Centre, CentreTest, DiagnosticTest
from app.schemas import (CentreCreate, CentreOut, CentreTestOut, DiagnosticTestCreate,
                         DiagnosticTestOut, Page, PriceIn)

router = APIRouter(tags=["catalogue"])
NS = "centres"


def _centre_out(c: Centre) -> CentreOut:
    return CentreOut(id=c.id, name=c.name, city=c.city, address=c.address, tests=[
        CentreTestOut(test_id=o.test_id, code=o.test.code, name=o.test.name, price=o.price)
        for o in c.offerings if o.is_active])


def _paginate(db: Session, stmt, page: int, page_size: int):
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return db.scalars(stmt.limit(page_size).offset((page - 1) * page_size)).all(), total


# ---------------- tests ----------------
@router.get("/tests", response_model=Page[DiagnosticTestOut])
def list_tests(q: str | None = Query(None, min_length=1, max_length=50),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db)):
    stmt = select(DiagnosticTest).order_by(DiagnosticTest.name)
    if q:
        stmt = stmt.where(DiagnosticTest.name.ilike(f"%{q}%"))
    items, total = _paginate(db, stmt, page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/tests", response_model=DiagnosticTestOut, status_code=201, dependencies=[Depends(require_admin)])
def create_test(data: DiagnosticTestCreate, db: Session = Depends(get_db)):
    if db.scalar(select(DiagnosticTest.id).where(func.lower(DiagnosticTest.code) == data.code.lower())):
        raise conflict("A test with this code already exists", "test_code_taken")
    test = DiagnosticTest(code=data.code.upper(), name=data.name, description=data.description)
    db.add(test)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("A test with this code already exists", "test_code_taken")
    return test


# ---------------- centres ----------------
@router.get("/centres", response_model=Page[CentreOut])
def list_centres(city: str | None = Query(None, max_length=80), test_id: int | None = Query(None, gt=0),
                 q: str | None = Query(None, min_length=1, max_length=50),
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 db: Session = Depends(get_db)):
    key = cache.ns_key(NS, f"list:{(city or '').lower()}:{test_id}:{(q or '').lower()}:{page}:{page_size}")
    if (hit := cache.cache_get(key)) is not None:
        return hit
    stmt = select(Centre).order_by(Centre.name, Centre.id)
    if city:
        stmt = stmt.where(func.lower(Centre.city) == city.lower())
    if q:
        stmt = stmt.where(Centre.name.ilike(f"%{q}%"))
    if test_id:
        stmt = stmt.where(Centre.offerings.any(and_(CentreTest.test_id == test_id, CentreTest.is_active)))
    items, total = _paginate(db, stmt, page, page_size)
    result = Page[CentreOut](items=[_centre_out(c) for c in items], total=total, page=page, page_size=page_size)
    cache.cache_set(key, result.model_dump(mode="json"))
    return result


@router.get("/centres/{centre_id}", response_model=CentreOut)
def get_centre(centre_id: int, db: Session = Depends(get_db)):
    key = cache.ns_key(NS, f"detail:{centre_id}")
    if (hit := cache.cache_get(key)) is not None:
        return hit
    centre = db.get(Centre, centre_id)
    if centre is None:
        raise not_found("Centre not found", "centre_not_found")
    out = _centre_out(centre)
    cache.cache_set(key, out.model_dump(mode="json"))
    return out


@router.post("/centres", response_model=CentreOut, status_code=201, dependencies=[Depends(require_admin)])
def create_centre(data: CentreCreate, db: Session = Depends(get_db)):
    centre = Centre(name=data.name, city=data.city, address=data.address)
    db.add(centre)
    db.commit()
    cache.invalidate(NS)
    return _centre_out(centre)


@router.put("/centres/{centre_id}/tests/{test_id}", response_model=CentreOut,
            dependencies=[Depends(require_admin)], summary="Offer a test at a centre / change its price")
def set_offering(centre_id: int, test_id: int, data: PriceIn, db: Session = Depends(get_db)):
    centre = db.get(Centre, centre_id)
    if centre is None:
        raise not_found("Centre not found", "centre_not_found")
    if db.get(DiagnosticTest, test_id) is None:
        raise not_found("Test not found", "test_not_found")
    offering = db.scalar(select(CentreTest).where(CentreTest.centre_id == centre_id, CentreTest.test_id == test_id))
    if offering:
        offering.price, offering.is_active = data.price, True  # existing bookings keep their price snapshot
    else:
        db.add(CentreTest(centre_id=centre_id, test_id=test_id, price=data.price))
    db.commit()
    db.refresh(centre)
    cache.invalidate(NS)
    return _centre_out(centre)


@router.delete("/centres/{centre_id}/tests/{test_id}", status_code=204, dependencies=[Depends(require_admin)],
               summary="Stop offering a test at a centre (soft delete)")
def remove_offering(centre_id: int, test_id: int, db: Session = Depends(get_db)):
    offering = db.scalar(select(CentreTest).where(CentreTest.centre_id == centre_id, CentreTest.test_id == test_id))
    if offering is None:
        raise not_found("Offering not found", "offering_not_found")
    offering.is_active = False
    db.commit()
    cache.invalidate(NS)
    return Response(status_code=204)
