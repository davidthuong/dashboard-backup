from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings, validate_runtime_security
from app.db import SessionLocal, init_db
from app.routers import agent_api, agents, auth, dashboard, ingest
from app.services.agent_schedule import AgentAutoTrigger
from app.services.agent_trigger import AgentTriggerService
from app.services.alerts import AlertManager
from app.services.collector import BackupCollector
from app.services.directadmin_parser import DirectAdminParser
from app.services.polling import PollingManager
from app.services.rclone_parser import RcloneParser
from app.services.veeam_name_sync import sync_veeam_target_names
from app.services.veeam_client import VeeamClient

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_security(settings)
    init_db()

    veeam_client = VeeamClient(settings)
    rclone_parser = RcloneParser(settings)
    directadmin_parser = DirectAdminParser(settings)
    collector = BackupCollector(veeam_client=veeam_client, rclone_parser=rclone_parser, directadmin_parser=directadmin_parser)
    alert_manager = AlertManager(settings=settings)
    agent_trigger_service = AgentTriggerService(settings=settings)
    agent_auto_trigger = AgentAutoTrigger(
        trigger_service=agent_trigger_service,
        alert_manager=alert_manager,
        settings=settings,
        db_factory=SessionLocal,
    )
    polling_manager = PollingManager(
        collector=collector,
        alert_manager=alert_manager,
        db_factory=SessionLocal,
        settings=settings,
    )

    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.collector = collector
    app.state.alert_manager = alert_manager
    app.state.agent_trigger_service = agent_trigger_service
    app.state.polling_manager = polling_manager

    boot_db = SessionLocal()
    try:
        veeam_sync = sync_veeam_target_names(boot_db, settings)
        if veeam_sync.changed_targets or veeam_sync.renamed_rows:
            logger.info(
                "Veeam name sync done: tracked=%s changed_targets=%s renamed_rows=%s",
                veeam_sync.tracked_targets,
                veeam_sync.changed_targets,
                veeam_sync.renamed_rows,
            )
    finally:
        boot_db.close()

    if settings.pull_collection_enabled:
        polling_manager.start()
        try:
            await polling_manager.run_collection()
        except Exception:
            logger.exception("Initial collection failed during startup.")
    else:
        logger.info("Pull collection is disabled (ingest-only mode). Scheduler is not started.")

    # Independent of pull collection: ingest-only hubs still need agents triggered and watched.
    agent_auto_trigger.start()

    yield

    agent_auto_trigger.shutdown()
    polling_manager.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(ingest.router)
app.include_router(agents.router)
app.include_router(agent_api.router)
