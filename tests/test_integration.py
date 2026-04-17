from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from runic import Conduit, DefaultError, Err, Ok, create_bus, create_conjurer
from runic.result import Result


@dataclass(slots=True)
class WidgetResult:
    widget_id: str
    status: str


@dataclass(slots=True)
class GetWidget:
    widget_id: str


class TestIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_conjurer_service_and_conduit_can_be_composed(self) -> None:
        bus = create_bus(dict)
        conjurer = create_conjurer()
        events = bus.subscribe()

        conduit = Conduit(bus)
        status_events = conduit.status_events()
        log_events = conduit.log_events()

        class WidgetService:
            async def emit(self, data: GetWidget) -> Result[WidgetResult, DefaultError]:
                if data.widget_id != "widget-1":
                    return Err(DefaultError(message=f"Unknown widget: {data.widget_id}", code="missing_widget"))
                return Ok(WidgetResult(widget_id=data.widget_id, status="ready"))

        handler, _ = conjurer.conjure(WidgetService())
        found = await handler.emit(GetWidget(widget_id="widget-1"))
        missing = await handler.emit(GetWidget(widget_id="widget-2"))

        async def work(ctx):
            await ctx.log("loading widget")
            await ctx.emit("widget_ready", {"widget_id": "widget-1"})
            return Ok({"done": True})

        try:
            spell_id = await conduit.invoke(work)
            await asyncio.wait_for(self._wait_for_spell(conduit, spell_id), timeout=1.0)
            spell_status_event = await asyncio.wait_for(anext(status_events), timeout=1.0)
            spell_log_event = await asyncio.wait_for(anext(log_events), timeout=1.0)
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

        self.assertEqual("spell_status", spell_status_event.name)
        self.assertEqual("running", spell_status_event.data.status)
        self.assertEqual("spell_log", spell_log_event.name)
        self.assertEqual("widget_ready", widget_ready_event.name)
        self.assertEqual({"widget_id": "widget-1"}, widget_ready_event.data)

    async def _wait_for_spell(self, conduit: Conduit, spell_id: str) -> None:
        while True:
            record = conduit.get_status(spell_id)
            if isinstance(record, Ok) and record.value.result == {"done": True}:
                return
            await asyncio.sleep(0.01)
