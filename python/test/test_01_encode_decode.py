from telekinesis import Broker, Telekinesis, Connection, Session, Entrypoint
import asyncio
import pytest
from telekinesis.helpers import create_entrypoint

pytestmark = pytest.mark.asyncio

async def run_encode_decode_test(host: str, port: int):
    """Test encode/decode functionality with specified host (determines transport type)."""
    # Determine URL based on host
    if host.startswith(('http://', 'https://')):
        url = f"{host}:{port}"
    else:
        url = f"ws://{host}:{port}"
    
    print(f"  Testing with {url}")

    async with Broker() as bro:
        await bro.serve(host, port=port)
        class Registry(dict):
            pass

        bro.entrypoint, _ = await create_entrypoint(Registry(), url)

        async with Entrypoint(url) as registry:
            await registry.update({"echo_type": lambda x: type(x).__name__})
            
            async with Entrypoint(url) as echo_type_ep:
                echo_type = await echo_type_ep.get("echo_type")

                assert await echo_type(1) == 'int'
                assert await echo_type(1.0) == 'float'
                assert await echo_type('one') == 'str'

            await registry.update({"echo_key_type": lambda obj: type(list(obj.keys())[0]).__name__})
            
            async with Entrypoint(url) as echo_key_type_ep:
                echo_key_type = await echo_key_type_ep.get("echo_key_type")
                assert await echo_key_type({(1, 2): '123'}) == 'tuple'

async def test_encode_decode_websocket():
    """Test encode/decode with WebSocket transport."""
    print("🔧 Testing encode/decode with WebSocket")
    await run_encode_decode_test("localhost", 8784)

async def test_encode_decode_http():
    """Test encode/decode with HTTP fallback transport."""
    print("🔧 Testing encode/decode with HTTP fallback")
    await run_encode_decode_test("http://localhost", 8785)
