"""Celery application and the polling schedule (docs/data.md, "Polling budget")."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

celery_app = Celery("belate", broker=get_settings().redis_url, include=["app.worker.tasks"])

celery_app.conf.update(
    # Schedules below are written in Paris time, like the commute they follow.
    timezone="Europe/Paris",
    task_ignore_result=True,
    worker_hijack_root_logger=False,
    # Each entry expires before its next run: a poll that could not start on time is
    # useless later, and the next one replaces it.
    beat_schedule={
        # PRIM rebuilds the global feed one second after each even minute. Starting
        # 20 seconds later gets every version while it is fresh.
        "poll-global": {
            "task": "app.worker.tasks.poll_global",
            "schedule": crontab(minute="*/2"),
            "options": {"countdown": 20, "expires": 110},
        },
        "poll-unitary": {
            "task": "app.worker.tasks.poll_unitary",
            "schedule": crontab(minute="*", hour="7-9,17-19"),
            "options": {"expires": 50},
        },
        # After the global poll of the same minute has been stored.
        "evaluate-alerts": {
            "task": "app.worker.tasks.evaluate_alerts",
            "schedule": crontab(minute="*/2"),
            "options": {"countdown": 60, "expires": 110},
        },
        "poll-traffic-info": {
            "task": "app.worker.tasks.poll_traffic_info",
            "schedule": crontab(minute="*/5"),
            "options": {"expires": 240},
        },
    },
)
