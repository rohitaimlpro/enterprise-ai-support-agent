"""Read-only endpoint so the UI can show the user's order history
alongside what the agent reports via the lookup_order tool."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Order, User
from app.schemas import OrderResponse

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderResponse])
def list_orders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Order)
        .filter(Order.user_id == current_user.id)
        .order_by(Order.order_date.desc())
        .all()
    )
