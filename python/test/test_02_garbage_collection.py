from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def run_garbage_collection_test(host: str, port: int):
    """Test garbage collection functionality with specified host (determines transport type)."""
    # Determine URL based on host
    if host.startswith(('http://', 'https://')):
        url = f"{host}:{port}"
    else:
        url = f"ws://{host}:{port}"
    
    print(f"  Testing garbage collection with {url}")

    async with Broker() as bro:
        await bro.serve(host, port=port)

        class Registry(dict):
            pass

        bro.entrypoint, _ = await create_entrypoint(Registry(), url)

        class Counter:
            def __init__(self, initial_value=0):
                self.value = initial_value

            def increment(self, amount=1):
                self.value += amount
                return self

        async with Entrypoint(url) as registry:
            await registry.update({"Counter": Counter})

            assert len(registry._session.targets) == 1

            async with Entrypoint(url) as counter_ep:
                counter = await counter_ep.get("Counter")(2)

                assert len(registry._session.targets) == 2

                del counter

            await asyncio.sleep(0.1)
            assert len(registry._session.targets) == 1


async def test_garbage_collection_websocket():
    """Test garbage collection with WebSocket transport."""
    print("🗑️  Testing garbage collection with WebSocket")
    await run_garbage_collection_test("localhost", 8781)


async def test_garbage_collection_http():
    """Test garbage collection with HTTP fallback transport."""
    print("🗑️  Testing garbage collection with HTTP fallback")
    await run_garbage_collection_test("http://localhost", 8782)
