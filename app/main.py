from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException, Query, Request

from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.notifier import FeishuNotifier
from app.provider import DirectWebsiteProvider
from app.service import MonitorService

settings = Settings.from_env()
configure_logging(settings.log_file, settings.log_retention_days)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database = Database(settings.database_path)
    provider = DirectWebsiteProvider(settings)
    notifier = FeishuNotifier(settings)
    service = MonitorService(settings, settings.load_hotels(), provider, notifier, database)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        service.run_once,
        CronTrigger(minute=settings.cron_minute, timezone=settings.timezone),
        kwargs={"trigger": "schedule"},
        id="hourly-hotel-check",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
    )
    scheduler.start()
    app.state.database = database
    app.state.service = service
    app.state.scheduler = scheduler
    if settings.run_on_startup:
        asyncio.create_task(service.run_once(trigger="startup"))
    logger.info("Monitor service started with %d hotels", len(service.hotels))
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await provider.close()
        await notifier.close()


app = FastAPI(title="Lake Tekapo Hotel Monitor", version="1.0.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status(request: Request) -> dict[str, object]:
    database: Database = request.app.state.database
    scheduler: AsyncIOScheduler = request.app.state.scheduler
    job = scheduler.get_job("hourly-hotel-check")
    return {
        "monitoring": {
            "check_in": settings.check_in.isoformat(),
            "check_out": settings.check_out.isoformat(),
            "timezone": settings.timezone,
            "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        },
        "latest_run": (database.latest_runs(1) or [None])[0],
        "hotels": database.snapshots(),
    }


@app.get("/runs")
def runs(request: Request, limit: int = Query(24, ge=1, le=500)) -> list[dict[str, object]]:
    return request.app.state.database.latest_runs(limit)


@app.post("/check")
async def check_now(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid X-Admin-Token")
    return await request.app.state.service.run_once(trigger="manual")
