import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, get_settings
from app.database import init_db
from app.deps import close_clients
from app.jobs import start_scheduler
from app.routers import api, pages
from app.services.stream_link import record_result, tonight_score
from app.stream.server import create_app as create_stream_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = start_scheduler() if get_settings().scheduler_enabled else None
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await close_clients()


app = FastAPI(
    title="MTG Collection Manager",
    description="Track what your Magic collection is worth and get alerted when wishlist cards get cheap.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(api.router)
app.include_router(pages.router)
# The token overlay for OBS: /stream/control (control panel) and /stream/overlay (Browser source).
stream_app = create_stream_app(get_settings().stream_state)
stream_app.state.result_hook = record_result  # Victory! on stream also logs the game
stream_app.state.tonight_hook = tonight_score  # tonight's score on stream
app.mount("/stream", stream_app)
