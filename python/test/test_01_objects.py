from telekinesis import Hub, Portal
import asyncio
import time
import pytest

pytestmark = pytest.mark.asyncio

@pytest.fixture
def event_loop(): # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()

async def test_serve_object_call():
    async with Hub() as hub:
        async with Portal() as portal:
            class TestCounter:
                def __init__(self, initial_value=0):
                    self.value = initial_value
                def increment(self, amount=1):
                    self.value += amount

            service = await portal.publish('TestCounter', TestCounter, n_workers=1)

            TestCounterClass = await portal.get('TestCounter')

            counter = await TestCounterClass()

            assert counter.value == 0
            await counter.increment()
            assert counter.value == 1
            await counter.increment(10)
            assert counter.value == 11

            await counter._close()

            await asyncio.sleep(0.1)

            assert len(hub.services['TestCounter']['workers']) == 1

            counter = await TestCounterClass(10)

            assert counter.value == 10
            await counter.increment()
            assert counter.value == 11
            await counter.increment(10)
            assert counter.value == 21

            service.stop_all()

# async def test_serve_recursive_object():
#     async with Hub():
#         async with Portal() as server:
#             class TestCounterWithClone:
#                 def __init__(self, initial_value=0):
#                     self.value = initial_value
#                 def increment(self, amount=1):
#                     self.value += amount
#                 def clone(self):
#                     return TestCounterWithClone(self.value)

#             service = await server.publish('TestCounter', TestCounterWithClone, n_workers=1)

#             async with Portal() as client:
#                 TestCounter = await client.get('TestCounter')

#                 counter_parent = await TestCounter(10)

#                 assert counter_parent.value == 10
#                 await counter_parent.increment(3)

#                 assert counter_parent.value == 13

#                 counter_child = await counter_parent.clone()
#                 await counter_parent.increment(3)

#                 assert counter_parent.value == 16
#                 assert counter_child.value == 13

#             service.stop_all()

# async def test_serve_updating_object():
#     async with Hub():
#         async with Portal() as server:
#             class TestCounterWithDelay:
#                 def __init__(self, initial_value=0):
#                     self.value = initial_value
#                 def increment(self, amount=1):
#                     async def delayed_inc(obj, amount):
#                         await asyncio.sleep(0.1)
#                         obj.value += amount
#                     asyncio.create_task(delayed_inc(self, amount))

#             service = await server.publish('TestCounter', TestCounterWithDelay, n_workers=1)

#             async with Portal() as client:
#                 TestCounter = await client.get('TestCounter')

#                 counter = await TestCounter(10)

#                 assert counter.value == 10
#                 await counter.increment(3)

#                 assert counter.value == 10

#                 await asyncio.sleep(0.15) # await counter._await_update()
#                 # await counter.increment(0) # Remote object should self update
#                 assert counter.value == 13

#             service.stop_all()
