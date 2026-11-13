"""Adaptador de APScheduler para ejecutar la sincronización diaria."""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class DailySyncScheduler:
    """Gestiona el ciclo de vida del cronjob diario con BackgroundScheduler."""

    def __init__(self, hour: int, minute: int, job_fn: Callable[[], object]) -> None:
        self._hour = hour
        self._minute = minute
        self._job_fn = job_fn
        self._scheduler = BackgroundScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._job_fn,
            trigger=CronTrigger(hour=self._hour, minute=self._minute),
            id="dgii_daily_sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._scheduler.start()
        logger.info(
            "Cronjob de sincronización diaria configurado para las %02d:%02d.",
            self._hour,
            self._minute,
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

