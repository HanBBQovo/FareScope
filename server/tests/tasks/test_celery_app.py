from app.tasks.celery_app import celery_app


def test_collection_tasks_are_registered_and_routed() -> None:
    celery_app.loader.import_default_modules()

    assert "farescope.collection.run" in celery_app.tasks
    assert "farescope.collection.scheduler_tick" in celery_app.tasks
    assert "farescope.collection.maintain_partitions" in celery_app.tasks
    assert "farescope.alerts.evaluate_pending" in celery_app.tasks
    assert "farescope.notifications.deliver_pending" in celery_app.tasks
    assert celery_app.conf.task_routes["farescope.collection.run"] == {
        "queue": "collector"
    }


def test_beat_has_scheduler_and_partition_maintenance() -> None:
    scheduled_tasks = {
        item["task"] for item in celery_app.conf.beat_schedule.values()
    }

    assert {
        "farescope.collection.scheduler_tick",
        "farescope.collection.maintain_partitions",
    }.issubset(scheduled_tasks)
    assert "farescope.alerts.evaluate_pending" in scheduled_tasks
    assert "farescope.notifications.deliver_pending" in scheduled_tasks
