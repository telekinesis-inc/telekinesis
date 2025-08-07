from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
import os

from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio

async def run_state_diffing_test(host: str, port: int):
    """Test state diffing functionality with specified host (determines transport type)."""
    # Determine URL based on host
    if host.startswith(('http://', 'https://')):
        url = f"{host}:{port}"
    else:
        url = f"ws://{host}:{port}"
    
    print(f"  Testing state diffing with {url}")

    measures = {"count": 0, "size_kb": 0}

    class MeasuredBroker(Broker):
        async def handle_send(self, connection, message, source, destination):
            measures["count"] += 1
            measures["size_kb"] += len(message) / 2 ** 10
            return await super().handle_send(connection, message, source, destination)

    async with MeasuredBroker() as bro:
        await bro.serve(host, port=port)

        class Container:
            def set(self, attr, val):
                self.__setattr__(attr, val)
            def add_method(self, doc):
                self.method = lambda x: x
                self.method.__doc__ = doc

        bro.entrypoint, container = await create_entrypoint(Container(), url)

        async with Entrypoint(url) as registry_ep:
            registry = await registry_ep._subscribe()

            # Adjust thresholds based on transport type (HTTP has ~50% overhead)
            is_http = url.startswith('http')
            multiplier = 1.6 if is_http else 1.1  # 60% margin for HTTP vs 10% for WebSocket
            
            base_threshold = 2 ** 10  # 1024 KB
            
            assert measures["size_kb"] < base_threshold

            await container.set("x", os.urandom(2 ** 20))

            await registry
            assert base_threshold < measures["size_kb"] < multiplier * base_threshold
            assert registry.x._last_value == container.x._last_value

            await container.set("y", os.urandom(2 ** 20))

            await registry
            double_threshold = 2 * base_threshold  # 2048 KB
            assert double_threshold < measures["size_kb"] < multiplier * double_threshold
            assert registry.y._last_value == container.y._last_value

            await container.add_method("some docstring")
            
            await registry
            assert registry._state.methods.get('method')[1] == "some docstring"

            await container._session.close()


async def test_state_diffing_websocket():
    """Test state diffing with WebSocket transport."""
    print("🔄 Testing state diffing with WebSocket")
    await run_state_diffing_test("localhost", 8783)


async def test_state_diffing_http():
    """Test state diffing with HTTP fallback transport."""
    print("🔄 Testing state diffing with HTTP fallback")
    await run_state_diffing_test("http://localhost", 8788)

if __name__ == '__main__':
    asyncio.run(test_state_diffing_websocket())
