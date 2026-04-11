from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from wyvern import DefaultError, Err, JobManager, Ok, create_bus, create_dispatcher
from wyvern.result import Result


@dataclass(slots=True)
class WidgetResult:
    widget_id: str
    status: str


@dataclass(slots=True)
class GetWidget:
    widget_id: str


class TestIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_dispatcher_service_and_jobs_can_be_composed(self) -> None:
        bus = create_bus(dict)
        dispatcher = create_dispatcher()
        events = bus.subscribe()

        manager = JobManager(bus)
        status_events = manager.status_events()
        log_events = manager.log_events()

        class WidgetService:
            async def emit(self, data: GetWidget) -> Result[WidgetResult, DefaultError]:
                if data.widget_id != "widget-1":
                    return Err(DefaultError(message=f"Unknown widget: {data.widget_id}", code="missing_widget"))
                return Ok(WidgetResult(widget_id=data.widget_id, status="ready"))

        handler, _ = dispatcher.register(WidgetService())
        found = await handler.emit(GetWidget(widget_id="widget-1"))
        missing = await handler.emit(GetWidget(widget_id="widget-2"))

        async def work(ctx):
            await ctx.log("loading widget")
            await ctx.emit("widget_ready", {"widget_id": "widget-1"})
            return Ok({"done": True})

        try:
            job_id = await manager.start(work)
            await asyncio.wait_for(self._wait_for_job(manager, job_id), timeout=1.0)
            job_status_event = await asyncio.wait_for(anext(status_events), timeout=1.0)
            job_log_event = await asyncio.wait_for(anext(log_events), timeout=1.0)
            widget_ready_event = await asyncio.wait_for(anext(events), timeout=1.0)
        finally:
            await events.aclose()
            await status_events.aclose()
            await log_events.aclose()

        self.assertIsInstance(found, Ok)
        assert isinstance(found, Ok)
        self.assertEqual("ready", found.value.status)

        self.assertIsInstance(missing, Err)
        assert isinstance(missing, Err)
        self.assertEqual("Unknown widget: widget-2", missing.error.message)
        self.assertEqual("missing_widget", missing.error.code)

        self.assertEqual("job_status", job_status_event.name)
        self.assertEqual("running", job_status_event.data.status)
        self.assertEqual("job_log", job_log_event.name)
        self.assertEqual("widget_ready", widget_ready_event.name)
        self.assertEqual({"widget_id": "widget-1"}, widget_ready_event.data)

    async def _wait_for_job(self, manager: JobManager, job_id: str) -> None:
        while True:
            record = manager.get_status(job_id)
            if record and record.result == {"done": True}:
                return
            await asyncio.sleep(0.01)
