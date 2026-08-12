"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.observability.logging_config import configure_logging
from app.observability.metrics import instrument_app
from app.routers import (
    admin_router,
    auth_router,
    chat_router,
    eval_router,
    orders_router,
    tickets_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Beginner-friendly alternative to Alembic: create any missing tables
    # on startup. Fine for a demo project; a real production app would
    # use migrations once the schema stabilizes.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Enterprise AI Customer Support Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

instrument_app(app)

app.include_router(auth_router.router)
app.include_router(chat_router.router)
app.include_router(tickets_router.router)
app.include_router(orders_router.router)
app.include_router(eval_router.router)
app.include_router(admin_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
