"""
Creates a demo user with some synthetic orders and a ticket, so you can log
in and immediately ask the agent things like "where is my order?" without
manually creating data first. Also creates a separate admin account for
the admin trace viewer (GET /admin/traces).

Run with:  python -m app.data.seed
Safe to re-run -- each user is created independently and skipped if it
already exists, rather than one all-or-nothing check (an earlier version
of this script checked only the demo user and returned early, which
silently skipped creating the admin user on every re-run once the demo
user already existed -- a real bug caught while wiring up admin access).
"""

from datetime import datetime, timedelta, timezone

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Order, OrderStatus, Ticket, TicketPriority, TicketStatus, User

DEMO_EMAIL = "demo@meridiansuite.example"
DEMO_PASSWORD = "demo1234"

# A separate account, deliberately not the same login as the customer
# demo user above -- in a real deployment the person reviewing agent
# traces for security purposes isn't the same login as a customer's.
ADMIN_EMAIL = "admin@meridiansuite.example"
ADMIN_PASSWORD = "admin1234"


def _seed_demo_user(db) -> None:
    if db.query(User).filter(User.email == DEMO_EMAIL).first():
        print(f"Demo user already exists ({DEMO_EMAIL}), skipping.")
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


def _seed_admin_user(db) -> None:
    if db.query(User).filter(User.email == ADMIN_EMAIL).first():
        print(f"Admin user already exists ({ADMIN_EMAIL}), skipping.")
        return

    admin = User(
        email=ADMIN_EMAIL,
        full_name="Ava Admin",
        hashed_password=hash_password(ADMIN_PASSWORD),
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    print(f"Seeded admin user {ADMIN_EMAIL} / {ADMIN_PASSWORD}.")


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _seed_demo_user(db)
        _seed_admin_user(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
