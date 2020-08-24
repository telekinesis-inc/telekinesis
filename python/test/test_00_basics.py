from telekinesis import Hub, Portal
import asyncio
import time
import pytest

pytestmark = pytest.mark.asyncio

@pytest.fixture
def event_loop(): # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()

async def test_start_stop_hub():
    async with Hub() as hub:
        assert hub.server.is_serving()

async def test_portal_connect_disconnect():
    async with Hub() as hub:
        async with Portal() as portal:
            assert portal.session.is_connected()
            assert portal.session.public_key in hub.connections

async def test_serve_list_call_function():
    async with Hub() as hub:
        async with Portal() as portal:
            service = await portal.publish('echo', lambda x: x)

            assert 'echo' in hub.services
            assert 'echo' in await portal.list()

            caller = await portal.get('echo')

            assert 'x' == (await caller('x'))

            service.stop_all()

async def test_serve_call_function_parallel():
    async with Hub() as hub:
        async with Portal() as portal:
            async def slow_echo(x):
                await asyncio.sleep(0.5)
                return x

            service = await portal.publish('slow_echo', slow_echo, n_workers=2)

            assert 'slow_echo' in hub.services
            await asyncio.sleep(.01) 
            assert len(hub.services['slow_echo']['workers']) == 2

            caller = await portal.get('slow_echo')

            call_0 = asyncio.create_task(caller('x'))
            call_1 = asyncio.create_task(caller('x'))

            t0 = time.time()

            assert 'x' == (await call_0)
            assert 'x' == (await call_1)

            assert (time.time() - t0) < 1

            service.stop_all()

async def test_serve_call_function_series():
    async with Hub() as hub:
        async with Portal() as portal:
            async def slow_echo(x):
                await asyncio.sleep(0.5)
                return x

            service = await portal.publish('slow_echo', slow_echo, n_workers=1)

            assert 'slow_echo' in hub.services
            await asyncio.sleep(.01) 
            assert len(hub.services['slow_echo']['workers']) == 1

            caller = await portal.get('slow_echo')

            call_0 = asyncio.create_task(caller('x'))
            call_1 = asyncio.create_task(caller('x'))

            t0 = time.time()

            assert 'x' == (await call_0)
            assert 'x' == (await call_1)

            assert (time.time() - t0) > 1

            service.stop_all()

async def test_serve_call_function_large_args():
    async with Hub() as hub:
        async with Portal() as portal:
            service = await portal.publish('echo', lambda x: x)

            await asyncio.sleep(0.01)
            assert len(hub.services['echo']['workers']) >= 1

            caller = await portal.get('echo')

            assert 'x'*2**21 == (await caller('x'*2**21))
            service.stop_all()

async def test_serve_call_function_numpy_args():
    async with Hub() as hub:
        async with Portal() as portal:
            service = await portal.publish('echo', lambda x: x)

            await asyncio.sleep(0.01)
            assert len(hub.services['echo']['workers']) >= 1

            caller = await portal.get('echo')

            content = set('asdf')

            assert content == (await caller(content))
            service.stop_all()

