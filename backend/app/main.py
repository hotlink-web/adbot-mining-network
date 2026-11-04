from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.admin_submissions import router as admin_submissions_router
from backend.app.api.admin_settlements import router as admin_settlements_router
from backend.app.api.admin_withdrawals import router as admin_withdrawals_router
from backend.app.api.withdrawals import router as withdrawals_router
from backend.app.api.users import router as users_router
from backend.app.api.tasks import router as tasks_router
from backend.app.core.config import settings, PROJECT_ROOT
from backend.app.database.mining_db import init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.include_router(admin_submissions_router)
app.include_router(admin_settlements_router)
app.include_router(users_router)
app.include_router(tasks_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }

app.include_router(admin_withdrawals_router)
app.include_router(withdrawals_router)

# Telegram Mini App frontend
app.mount(
    "/assets",
    StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "assets")),
    name="mining_assets",
)


@app.get("/", include_in_schema=False)
def mining_mini_app():
    return FileResponse(PROJECT_ROOT / "frontend" / "index.html")
