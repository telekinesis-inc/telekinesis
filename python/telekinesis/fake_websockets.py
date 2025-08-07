"""
Fake WebSocket implementation using HTTP long-polling as fallback.
Provides websockets-compatible API using asyncio + stdlib only.
"""
import asyncio
import json
import logging
import ssl
import uuid
import time
from typing import Optional, Callable, Any, AsyncGenerator, Dict, Set
from urllib.parse import urlparse, parse_qs
from http import HTTPStatus


logger = logging.getLogger(__name__)


class FakeWebSocketConnection:
    """
    Fake WebSocket connection that emulates websockets API over HTTP.
    Supports both client and server-side connections.
    """
    
    def __init__(self, is_server: bool = False, connection_id: str = None):
        self.is_server = is_server
        self._closed = False
        self._message_queue = asyncio.Queue()
        self._close_event = asyncio.Event()
        self._connection_id = connection_id or str(uuid.uuid4())
        
        # For server connections
        self._peer_addr = None
        self._path = None
        self._server_ref = None
        
        # For client connections  
        self._url = None
        self._client_ref = None
        
    async def send(self, data: bytes) -> None:
        """Send binary data over the fake websocket connection."""
        if self._closed:
            logger.debug(f"Attempted to send on closed connection {self._connection_id}")
            raise ConnectionError("Connection is closed")
            
        logger.debug(f"FakeWS send: {len(data)} bytes (conn: {self._connection_id})")
        
        try:
            if self.is_server:
                # Server-side: add to connection's message queue for polling
                if self._server_ref:
                    await self._server_ref._queue_message_for_connection(self._connection_id, data)
                else:
                    logger.warning(f"No server reference for connection {self._connection_id}")
                    raise ConnectionError("Server connection lost")
            else:
                # Client-side: HTTP POST to server
                if self._client_ref:
                    await self._client_ref._http_send(data)
                else:
                    logger.warning(f"No client reference for connection {self._connection_id}")
                    raise ConnectionError("Client connection lost")
        except Exception as e:
            # Log but don't immediately fail - the connection might recover
            logger.debug(f"Temporary send error for connection {self._connection_id}: {e}")
            # Re-raise only if it's a permanent connection issue
            if "Connection closed" in str(e) or "Invalid connection_id" in str(e):
                raise
            # For other errors (like broker issues), just log and continue
    
    async def recv(self) -> bytes:
        """Receive binary data from the fake websocket connection.""" 
        if self._closed:
            logger.debug(f"Attempted to recv on closed connection {self._connection_id}")
            raise ConnectionError("Connection is closed")
            
        logger.debug(f"FakeWS recv: waiting for message (conn: {self._connection_id})")
            
        if self.is_server:
            # Server-side: messages come from the actual websocket handler
            try:
                # Wait for a message or close event
                message_task = asyncio.create_task(self._message_queue.get())
                close_task = asyncio.create_task(self._close_event.wait())
                
                done, pending = await asyncio.wait(
                    [message_task, close_task], 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                
                if close_task in done:
                    logger.debug(f"Connection {self._connection_id} closed during recv")
                    raise ConnectionError("Connection closed")
                    
                message = await message_task
                logger.debug(f"FakeWS recv: got {len(message)} bytes (conn: {self._connection_id})")
                return message
                
            except Exception as e:
                if not self._closed:
                    logger.debug(f"FakeWS server recv error for {self._connection_id}: {e}")
                raise ConnectionError("Connection closed")
        else:
            # Client-side: HTTP long-polling
            if self._client_ref:
                return await self._client_ref._http_poll()
            else:
                logger.warning(f"No client reference for connection {self._connection_id}")
                raise ConnectionError("No client reference")
    
    async def close(self) -> None:
        """Close the fake websocket connection."""
        if not self._closed:
            self._closed = True
            self._close_event.set()
            logger.debug(f"FakeWS connection closed (ID: {self._connection_id})")
            
            # Server-side cleanup
            if self.is_server and self._server_ref and self._connection_id:
                # Remove from server's connection tracking
                self._server_ref._connections.pop(self._connection_id, None)
                self._server_ref._connection_queues.pop(self._connection_id, None)
                handler_task = self._server_ref._handler_tasks.pop(self._connection_id, None)
                if handler_task and not handler_task.done():
                    handler_task.cancel()
    
    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        """Async iterator for receiving messages (server-side pattern)."""
        try:
            while not self._closed:
                try:
                    message = await self.recv()
                    yield message
                except ConnectionError:
                    break
        except Exception as e:
            logger.debug(f"FakeWS async iteration ended: {e}")
    
    # Properties for compatibility
    @property
    def closed(self) -> bool:
        """Check if connection is closed (legacy websockets API)."""
        return self._closed
        
    @property
    def remote_address(self) -> Optional[tuple]:
        """Get remote address (server-side)."""
        return self._peer_addr
        
    @property  
    def path(self) -> Optional[str]:
        """Get request path (server-side)."""
        return self._path


class FakeWebSocketServer:
    """
    Fake WebSocket server using HTTP endpoints.
    Mimics websockets.serve() behavior.
    """
    
    def __init__(self, handler: Callable, host: str, port: int, ssl_context: Optional[ssl.SSLContext] = None):
        self.handler = handler
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self._server = None
        self._connections: Dict[str, FakeWebSocketConnection] = {}
        self._connection_queues: Dict[str, asyncio.Queue] = {}
        self._handler_tasks: Dict[str, asyncio.Task] = {}
        
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle incoming HTTP connection and route to appropriate endpoint."""
        peer_addr = writer.get_extra_info('peername')
        logger.info(f"FakeWS server: new HTTP connection from {peer_addr}")
        
        try:
            # Read HTTP request
            request_line = await reader.readuntil(b'\r\n')
            method, path, version = request_line.decode().strip().split(' ', 2)
            
            # Read headers
            headers = {}
            while True:
                line = await reader.readuntil(b'\r\n')
                if line == b'\r\n':  # End of headers
                    break
                header_line = line.decode().strip()
                if ':' in header_line:
                    key, value = header_line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
            
            # Parse path and query parameters
            if '?' in path:
                path_part, query_part = path.split('?', 1)
                query_params = parse_qs(query_part)
            else:
                path_part = path
                query_params = {}
                
            # Route to appropriate handler
            if method == 'POST' and path_part == '/connect':
                await self._handle_connect(reader, writer, headers, query_params)
            elif method == 'POST' and path_part == '/send':
                await self._handle_send(reader, writer, headers, query_params)
            elif method == 'GET' and path_part == '/poll':
                await self._handle_poll(reader, writer, headers, query_params)
            elif method == 'POST' and path_part == '/close':
                await self._handle_close_request(reader, writer, headers, query_params)
            else:
                await self._send_http_response(writer, 404, b'Not Found')
                
        except Exception as e:
            logger.error(f"FakeWS HTTP error: {e}")
            try:
                await self._send_http_response(writer, 500, b'Internal Server Error')
            except:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass
    
    async def start(self) -> None:
        """Start the fake websocket server."""
        logger.info(f"Starting FakeWS server on {self.host}:{self.port} (SSL: {self.ssl_context is not None})")
        
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            ssl=self.ssl_context
        )
        
    def close(self) -> None:
        """Close the server."""
        if self._server:
            self._server.close()
            # Cancel all handler tasks
            for task in self._handler_tasks.values():
                if not task.done():
                    task.cancel()
            logger.info("FakeWS server closed")
    
    async def wait_closed(self) -> None:
        """Wait for server to close."""
        if self._server:
            await self._server.wait_closed()
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        self.close()
        await self.wait_closed()
        
    async def _queue_message_for_connection(self, connection_id: str, data: bytes) -> None:
        """Queue a message for a specific connection to be retrieved via polling."""
        if connection_id in self._connection_queues:
            try:
                await self._connection_queues[connection_id].put(data)
            except Exception as e:
                logger.error(f"Failed to queue message for connection {connection_id}: {e}")
                # Remove broken connection
                self._connections.pop(connection_id, None)
                self._connection_queues.pop(connection_id, None)
            
    async def _handle_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, 
                            headers: dict, query_params: dict) -> None:
        """Handle WebSocket connection establishment over HTTP."""
        # Generate new connection ID
        connection_id = str(uuid.uuid4())
        
        # Create fake websocket connection
        fake_ws = FakeWebSocketConnection(is_server=True, connection_id=connection_id)
        fake_ws._peer_addr = writer.get_extra_info('peername')
        fake_ws._path = "/"
        fake_ws._server_ref = self
        
        # Store connection and create message queue
        self._connections[connection_id] = fake_ws
        self._connection_queues[connection_id] = asyncio.Queue()
        
        # Start the WebSocket handler in the background
        handler_task = asyncio.create_task(self._run_handler(fake_ws))
        self._handler_tasks[connection_id] = handler_task
        
        # Return connection ID to client
        response_data = json.dumps({"connection_id": connection_id}).encode()
        await self._send_http_response(writer, 200, response_data, 
                                      headers={'Content-Type': 'application/json'})
        
    async def _run_handler(self, fake_ws: FakeWebSocketConnection) -> None:
        """Run the WebSocket handler for a fake connection."""
        connection_id = fake_ws._connection_id
        try:
            logger.debug(f"Starting handler for connection {connection_id}")
            await self.handler(fake_ws, fake_ws._path)
        except Exception as e:
            logger.error(f"FakeWS handler error for connection {connection_id}: {e}")
            # Don't immediately close the connection on handler errors
            # The handler might recover from temporary errors
        finally:
            # Clean up connection only if it's actually closed
            if fake_ws._closed:
                logger.debug(f"Cleaning up closed connection {connection_id}")
                self._connections.pop(connection_id, None)
                self._connection_queues.pop(connection_id, None) 
                self._handler_tasks.pop(connection_id, None)
            
    async def _handle_send(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          headers: dict, query_params: dict) -> None:
        """Handle sending data to a WebSocket connection."""
        connection_id = query_params.get('connection_id', [''])[0]
        if not connection_id or connection_id not in self._connections:
            await self._send_http_response(writer, 400, b'Invalid connection_id')
            return
            
        # Check if connection is still valid
        fake_ws = self._connections[connection_id]
        if fake_ws._closed:
            await self._send_http_response(writer, 400, b'Connection closed')
            return
            
        # Read message data
        content_length = int(headers.get('content-length', '0'))
        if content_length > 0:
            data = await reader.readexactly(content_length)
        else:
            data = b''
            
        try:
            # Send data to the connection's message queue
            await fake_ws._message_queue.put(data)
            await self._send_http_response(writer, 200, b'OK')
        except Exception as e:
            logger.error(f"Error sending to connection {connection_id}: {e}")
            await self._send_http_response(writer, 500, b'Send failed')
        
    async def _handle_poll(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          headers: dict, query_params: dict) -> None:
        """Handle long-polling for receiving data from a WebSocket connection."""
        connection_id = query_params.get('connection_id', [''])[0]
        if not connection_id or connection_id not in self._connection_queues:
            await self._send_http_response(writer, 400, b'Invalid connection_id')
            return
            
        # Check if connection is still valid
        if connection_id in self._connections and self._connections[connection_id]._closed:
            await self._send_http_response(writer, 400, b'Connection closed')
            return
            
        try:
            # Long-poll for a message with timeout
            message_queue = self._connection_queues[connection_id]
            message = await asyncio.wait_for(message_queue.get(), timeout=30.0)
            await self._send_http_response(writer, 200, message)
        except asyncio.TimeoutError:
            # Timeout - return empty response so client can poll again
            await self._send_http_response(writer, 204, b'')  # No Content
        except Exception as e:
            logger.error(f"Polling error for connection {connection_id}: {e}")
            await self._send_http_response(writer, 500, b'Polling error')
            
    async def _handle_close_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                   headers: dict, query_params: dict) -> None:
        """Handle closing a WebSocket connection."""
        connection_id = query_params.get('connection_id', [''])[0]
        if connection_id in self._connections:
            fake_ws = self._connections[connection_id]
            await fake_ws.close()
            
        await self._send_http_response(writer, 200, b'OK')
        
    async def _send_http_response(self, writer: asyncio.StreamWriter, status_code: int, 
                                 body: bytes, headers: dict = None) -> None:
        """Send an HTTP response."""
        response_headers = headers or {}
        response_headers.setdefault('Content-Length', str(len(body)))
        
        # Status line
        response = f"HTTP/1.1 {status_code} {HTTPStatus(status_code).phrase}\r\n".encode()
        
        # Headers
        for key, value in response_headers.items():
            response += f"{key}: {value}\r\n".encode()
        response += b"\r\n"
        
        # Body
        response += body
        
        writer.write(response)
        await writer.drain()


class FakeWebSocketClient:
    """
    Fake WebSocket client using HTTP requests.
    Mimics websockets.connect() behavior.
    """
    
    def __init__(self, url: str):
        self.url = url
        self.parsed_url = urlparse(url)
        self._connection = None
        self._connection_id = None
        self._poll_task = None
        
    async def connect(self) -> FakeWebSocketConnection:
        """Connect to fake websocket server."""
        logger.info(f"FakeWS client connecting to {self.url}")
        
        # Convert ws://host:port to http://host:port for HTTP requests
        if self.url.startswith('ws://'):
            base_url = 'http://' + self.url[5:]
        elif self.url.startswith('wss://'):
            base_url = 'https://' + self.url[6:]
        else:
            base_url = self.url
            
        # Establish connection by POST to /connect
        reader, writer = await asyncio.open_connection(
            self.parsed_url.hostname,
            self.parsed_url.port or (443 if self.parsed_url.scheme == 'wss' else 80)
        )
        
        try:
            # Send HTTP POST /connect request
            request = (
                "POST /connect HTTP/1.1\r\n"
                f"Host: {self.parsed_url.netloc}\r\n"
                "Content-Length: 0\r\n"
                "\r\n"
            ).encode()
            
            writer.write(request)
            await writer.drain()
            
            # Read HTTP response
            response_line = await reader.readuntil(b'\r\n')
            status_code = int(response_line.decode().split()[1])
            
            # Read response headers
            headers = {}
            while True:
                line = await reader.readuntil(b'\r\n')
                if line == b'\r\n':  # End of headers
                    break
                header_line = line.decode().strip()
                if ':' in header_line:
                    key, value = header_line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
            
            # Read response body
            content_length = int(headers.get('content-length', '0'))
            if content_length > 0:
                response_body = await reader.readexactly(content_length)
                response_data = json.loads(response_body.decode())
                self._connection_id = response_data['connection_id']
            else:
                raise ConnectionError(f"Connection failed with status {status_code}")
                
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass
        
        # Create fake connection object
        self._connection = FakeWebSocketConnection(is_server=False, connection_id=self._connection_id)
        self._connection._url = self.url
        self._connection._client_ref = self
        
        return self._connection
    
    async def __aenter__(self) -> FakeWebSocketConnection:
        """Async context manager entry."""
        return await self.connect()
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._connection:
            await self._connection.close()
            
    async def _http_send(self, data: bytes) -> None:
        """Send data via HTTP POST to the server."""
        if not self._connection_id:
            logger.error("HTTP send: Not connected")
            raise ConnectionError("Not connected")
            
        logger.debug(f"HTTP send: {len(data)} bytes to connection {self._connection_id}")
            
        try:
            # Open connection for sending
            reader, writer = await asyncio.open_connection(
                self.parsed_url.hostname,
                self.parsed_url.port or (443 if self.parsed_url.scheme == 'wss' else 80)
            )
            
            try:
                # Send HTTP POST /send request
                request = (
                    f"POST /send?connection_id={self._connection_id} HTTP/1.1\r\n"
                    f"Host: {self.parsed_url.netloc}\r\n"
                    f"Content-Length: {len(data)}\r\n"
                    "\r\n"
                ).encode() + data
                
                writer.write(request)
                await writer.drain()
                
                # Read response (should be 200 OK)
                response_line = await reader.readuntil(b'\r\n')
                status_code = int(response_line.decode().split()[1])
                if status_code != 200:
                    logger.error(f"HTTP send failed with status {status_code}")
                    raise ConnectionError(f"Send failed with status {status_code}")
                else:
                    logger.debug(f"HTTP send successful for connection {self._connection_id}")
                    
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"HTTP send error for connection {self._connection_id}: {e}")
            raise ConnectionError(f"Send failed: {e}")
            
    async def _http_poll(self) -> bytes:
        """Poll for data via HTTP GET from the server."""
        if not self._connection_id:
            raise ConnectionError("Not connected")
            
        # Open connection for polling
        reader, writer = await asyncio.open_connection(
            self.parsed_url.hostname,
            self.parsed_url.port or (443 if self.parsed_url.scheme == 'wss' else 80)
        )
        
        try:
            # Send HTTP GET /poll request
            request = (
                f"GET /poll?connection_id={self._connection_id} HTTP/1.1\r\n"
                f"Host: {self.parsed_url.netloc}\r\n"
                "\r\n"
            ).encode()
            
            writer.write(request)
            await writer.drain()
            
            # Read HTTP response
            response_line = await reader.readuntil(b'\r\n')
            status_code = int(response_line.decode().split()[1])
            
            # Read response headers
            headers = {}
            while True:
                line = await reader.readuntil(b'\r\n')
                if line == b'\r\n':  # End of headers
                    break
                header_line = line.decode().strip()
                if ':' in header_line:
                    key, value = header_line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
            
            if status_code == 200:
                # Read response body (the message data)
                content_length = int(headers.get('content-length', '0'))
                if content_length > 0:
                    return await reader.readexactly(content_length)
                else:
                    return b''
            elif status_code == 204:  # No Content (timeout)
                # No message available, retry polling
                return await self._http_poll()
            elif status_code == 400:
                # Connection invalid - likely closed
                raise ConnectionError("Connection closed or invalid")
            else:
                # Other errors - treat as connection issues
                logger.debug(f"HTTP poll error: status {status_code}")
                raise ConnectionError(f"Connection error: HTTP {status_code}")
                
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass


# Public API functions (websockets-compatible)

async def fake_ws_serve(
    handler: Callable, 
    host: str = "localhost", 
    port: int = 8765, 
    ssl: Optional[ssl.SSLContext] = None,
    **kwargs
) -> FakeWebSocketServer:
    """
    Start a fake WebSocket server using HTTP fallback.
    
    Args:
        handler: Async function to handle connections: handler(websocket, path)
        host: Host to bind to
        port: Port to bind to  
        ssl: SSL context for HTTPS (optional)
        **kwargs: Additional arguments (ignored for compatibility)
        
    Returns:
        FakeWebSocketServer instance
    """
    server = FakeWebSocketServer(handler, host, port, ssl)
    await server.start()
    return server


async def fake_ws_connect(url: str, **kwargs) -> FakeWebSocketConnection:
    """
    Connect to a fake WebSocket server using HTTP fallback.
    
    Args:
        url: WebSocket URL (ws:// or wss://)
        **kwargs: Additional arguments (ignored for compatibility)
        
    Returns:
        FakeWebSocketConnection instance
    """
    client = FakeWebSocketClient(url)
    return await client.connect()


if __name__ == "__main__":
    # Simple test
    async def test_handler(websocket, path):
        print(f"Handler called with path: {path}")
        try:
            async for message in websocket:
                print(f"Server received: {message}")
                await websocket.send(b"Echo: " + message)
        except Exception as e:
            print(f"Handler error: {e}")
    
    async def test_fake_websockets():
        # Test HTTP fallback server
        server = await fake_ws_serve(test_handler, "localhost", 8765)
        print("Fake WebSocket server started on localhost:8765")
        
        # Give server a moment to start
        await asyncio.sleep(0.1)
        
        # Test HTTP client (use http:// to trigger fallback)  
        client_ws = await fake_ws_connect("http://localhost:8765")
        print("Fake WebSocket client connected via HTTP")

        # Test sending a message
        test_message = b'Hello from HTTP client!'
        print(f"Sending: {test_message}")
        await client_ws.send(test_message)
        
        # Test receiving (should get echo back)
        print("Waiting for response...")
        try:
            response = await asyncio.wait_for(client_ws.recv(), timeout=5.0)
            print(f"Client received: {response}")
        except asyncio.TimeoutError:
            print("Timeout waiting for response")
        
        # Cleanup
        await client_ws.close()
        server.close()
        await server.wait_closed()
        print("Test completed")
    
    # Run test
    asyncio.run(test_fake_websockets())

