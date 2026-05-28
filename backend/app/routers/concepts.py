"""Concept hierarchy endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Concept
from backend.app.schemas import ConceptLeaf, ConceptParent


router = APIRouter(prefix="/concepts", tags=["concepts"])


@router.get("", response_model=list[ConceptParent])
def list_concepts(db: Session = Depends(get_db)) -> list[ConceptParent]:
    """Return the full concept hierarchy: 8 parents, each with their leaves.

    Ordered by name within each level. Stable across requests so the
    frontend can render a deterministic chart layout.
    """
    parents = db.execute(
        select(Concept).where(Concept.level == 0).order_by(Concept.name)
    ).scalars().all()

    result: list[ConceptParent] = []
    for parent in parents:
        leaves = db.execute(
            select(Concept)
            .where(Concept.parent_id == parent.id)
            .order_by(Concept.name)
        ).scalars().all()
        result.append(
            ConceptParent(
                slug=parent.slug,
                name=parent.name,
                leaves=[ConceptLeaf(slug=leaf.slug, name=leaf.name) for leaf in leaves],
            )
        )
    return result
