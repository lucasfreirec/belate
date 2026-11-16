from celery.schedules import crontab

from app.ingest.polling import QUOTA_LIMITS
from app.prim.models import Api
from app.worker import tasks  # noqa: F401  (registers the tasks)
from app.worker.celery_app import celery_app

SCHEDULE = celery_app.conf.beat_schedule


def runs_per_day(entry: str) -> int:
    schedule = SCHEDULE[entry]["schedule"]
    assert isinstance(schedule, crontab)
    # celery-types does not declare the parsed minute and hour sets.
    return len(schedule.minute) * len(schedule.hour)  # type: ignore[attr-defined]


def test_every_scheduled_task_exists() -> None:
    for entry in SCHEDULE.values():
        assert entry["task"] in celery_app.tasks


def test_the_schedule_fits_in_the_daily_budgets() -> None:
    runs = {
        Api.GLOBAL: runs_per_day("poll-global"),
        Api.UNITARY: runs_per_day("poll-unitary"),
        Api.TRAFFIC_INFO: runs_per_day("poll-traffic-info"),
    }

    assert runs == {Api.GLOBAL: 720, Api.UNITARY: 360, Api.TRAFFIC_INFO: 288}
    for api, count in runs.items():
        limit = QUOTA_LIMITS[api]
        assert count <= limit.daily - limit.retry_reserve


def test_the_minimum_interval_is_shorter_than_the_schedule() -> None:
    # Otherwise the quota guard would refuse regular polls as "too soon". The global task
    # starts 20 seconds after its schedule, which can shift two runs closer together.
    assert QUOTA_LIMITS[Api.GLOBAL].min_interval < 120 - 20
    assert QUOTA_LIMITS[Api.UNITARY].min_interval < 60
    assert QUOTA_LIMITS[Api.TRAFFIC_INFO].min_interval < 300


def test_each_poll_expires_before_the_next_one() -> None:
    assert SCHEDULE["poll-global"]["options"]["expires"] < 120
    assert SCHEDULE["poll-unitary"]["options"]["expires"] < 60
    assert SCHEDULE["poll-traffic-info"]["options"]["expires"] < 300
