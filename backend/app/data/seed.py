"""
Creates a demo user with some synthetic orders and a ticket, so you can log
in and immediately ask the agent things like "where is my order?" without
manually creating data first.

Run with:  python -m app.data.seed
Safe to re-run -- skips creation if the demo user already exists.
"""

from datetime import datetime, timedelta, timezone

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Order, OrderStatus, Ticket, TicketPriority, TicketStatus, User

DEMO_EMAIL = "demo@meridiansuite.example"
DEMO_PASSWORD = "demo1234"


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if existing:
            print(f"Demo user already exists ({DEMO_EMAIL}), skipping seed.")
            return

        user = User(
            email=DEMO_EMAIL,
            full_name="Dana Demo",
            hashed_password=hash_password(DEMO_PASSWORD),
        )
        db.add(user)
        db.flush()  # populate user.id before using it below

        now = datetime.now(timezone.utc)
        orders = [
            Order(
                user_id=user.id,
                product_name="Meridian Suite Team Plan (annual)",
                status=OrderStatus.delivered,
                amount=280.0,
                order_date=now - timedelta(days=40),
            ),
            Order(
                user_id=user.id,
                product_name="Graphics Tablet Pro",
                status=OrderStatus.shipped,
                amount=249.99,
                order_date=now - timedelta(days=2),
            ),
            Order(
                user_id=user.id,
                product_name="Premium Font & Template Pack",
                status=OrderStatus.processing,
                amount=4.99,
                order_date=now - timedelta(hours=3),
            ),
        ]
        db.add_all(orders)

        ticket = Ticket(
            user_id=user.id,
            subject="Sync not working across devices",
            description="Files stopped syncing between my laptop and desktop yesterday.",
            status=TicketStatus.open,
            priority=TicketPriority.medium,
            created_at=now - timedelta(days=1),
        )
        db.add(ticket)

        db.commit()
        print(f"Seeded demo user {DEMO_EMAIL} / {DEMO_PASSWORD} with 3 orders and 1 ticket.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
