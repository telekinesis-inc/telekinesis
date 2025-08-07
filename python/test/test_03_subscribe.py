from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
import os

from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio


@pytest.fixture
def event_loop():  # This avoids 'Task was destroyed but it is pending!' message
    yield asyncio.get_event_loop()


async def run_subscribe_test(host: str, port: int):
    """Test subscribe functionality with specified host (determines transport type)."""
    # Determine URL based on host
    if host.startswith(('http://', 'https://')):
        url = f"{host}:{port}"
    else:
        url = f"ws://{host}:{port}"
    
    print(f"  Testing subscribe with {url}")

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
            await registry.update({"counter": Counter(2)})

            async with Entrypoint(url) as counter0_ep:
                counter0 = await counter0_ep.get("counter")
                
                async with Entrypoint(url) as counter1_ep:
                    counter1 = await counter1_ep.get("counter")._subscribe()

                    assert counter1.value._last_value == 2

                    await counter0.increment(3).increment(1)

                    await asyncio.sleep(0.1)
                    assert counter1.value._last_value == 6


async def test_subscribe_websocket():
    """Test subscribe with WebSocket transport."""
    print("📡 Testing subscribe with WebSocket")
    await run_subscribe_test("localhost", 8780)


async def test_subscribe_http():
    """Test subscribe with HTTP fallback transport."""
    print("📡 Testing subscribe with HTTP fallback")
    await run_subscribe_test("http://localhost", 8783)

