from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio


async def run_forwarding_test(host: str, port: int):
    """Test request forwarding functionality with specified host (determines transport type)."""
    # Determine URL based on host
    if host.startswith(('http://', 'https://')):
        url = f"{host}:{port}"
    else:
        url = f"ws://{host}:{port}"
    
    print(f"  Testing request forwarding with {url}")

    count = {"count": 0}

    class CountedBroker(Broker):
        async def handle_send(self, *args, **kwargs):
            count["count"] += 1
            return await super().handle_send(*args, **kwargs)

    async with CountedBroker() as bro:
        await bro.serve(host, port=port)

        class Registry(dict):
            pass

        bro.entrypoint, _ = await create_entrypoint(Registry(), url)

        echo = lambda x: x

        async with Entrypoint(url) as registry:
            await registry.update({"echo": echo})

            count["count"] = 0
            async with Entrypoint(url) as echo_ep:
                result = await echo_ep.get("echo")("hello")
                assert result == "hello"
                assert count["count"] == 2 * 3


async def test_forwarding_websocket():
    """Test request forwarding with WebSocket transport."""
    print("📤 Testing request forwarding with WebSocket")
    await run_forwarding_test("localhost", 8782)


async def test_forwarding_http():
    """Test request forwarding with HTTP fallback transport."""
    print("📤 Testing request forwarding with HTTP fallback")
    await run_forwarding_test("http://localhost", 8787)

