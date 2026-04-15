from __future__ import annotations

import asyncio
import unittest

from runic import Event, create_bus


class TestEventBus(unittest.IsolatedAsyncioTestCase):
    def test_create_bus_initializes_shape_and_empty_subscribers(self) -> None:
        bus = create_bus(dict)

        self.assertIs(dict, bus._shape)
        self.assertEqual([], bus._subscribers)

    async def test_publish_subscribe_fanout(self) -> None:
        bus = create_bus(dict)
        first = bus.subscribe()
        second = bus.subscribe()

        self.assertEqual(2, len(bus._subscribers))

        try:
            await bus.publish(Event(name="update", data={"step": 1}))
            event_one = await anext(first)
            event_two = await anext(second)
        finally:
            await first.aclose()
            await second.aclose()

        self.assertEqual("update", event_one.name)
        self.assertEqual({"step": 1}, event_one.data)
        self.assertEqual({"step": 1}, event_two.data)
        self.assertEqual([], bus._subscribers)

    async def test_subscriber_cleanup_keeps_bus_usable(self) -> None:
        bus = create_bus(dict)
        subscriber = bus.subscribe()
        await bus.publish(Event(name="update", data={"step": 2}))
        event = await asyncio.wait_for(anext(subscriber), timeout=1.0)
        await subscriber.aclose()

        self.assertEqual([], bus._subscribers)
        self.assertEqual({"step": 2}, event.data)

        survivor = bus.subscribe()
        try:
            await bus.publish(Event(name="update", data={"step": 3}))
            event = await asyncio.wait_for(anext(survivor), timeout=1.0)
        finally:
            await survivor.aclose()

        self.assertEqual({"step": 3}, event.data)

    async def test_type_enforcement(self) -> None:
        bus = create_bus(dict)
        with self.assertRaises(TypeError):
            await bus.publish(Event(name="invalid", data="wrong"))  # type: ignore[arg-type]
