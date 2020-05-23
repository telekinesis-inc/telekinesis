import websockets
# import asyncio
import json
from uuid import uuid4
from collections import deque
import time
import http
from .encryption import verify

class Hub():
    def __init__(self, master_pubkeys=None, parent=None, peers=None, path_privkey=None, unautheticated_message='OK'):
        self.master_pubkeys = master_pubkeys if (isinstance(master_pubkeys, list) 
                                                 or master_pubkeys is None ) \
                                             else [master_pubkeys]

        self.unautheticated_message = unautheticated_message
        self.static = {}
        self.connections = {}
        self.services = {}
        self.server = None

    def str_uuid(self, n):# TODO avoid collissions
        BASE = 'QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm1234567890'
        return ''.join([BASE[uuid4().int//(len(BASE)**i)%len(BASE)] 
                        for i in range(n)])
    
    async def handle_incoming(self, path, headers):
        if path[:3] == 'ws/':
            return None
        route = path.split('?')[0]
        route = route[:-1] if route[-1] == '/' and len(route) > 1 else route
        query = '?'.join(path.split('?')[1:])
        if route in self.static:
            page = self.static[route]
            if 'function' in page:
                await self.assign_call(page['function'], 
                        kwargs={kv.split('=')[0]: '='.join(kv.split('=')[1:]) for kv in query.split('&')})

            return http.HTTPStatus(page['code'] if 'code' in page else 200), \
                page['headers'] if 'headers' in page else {'Access-Control-Allow-Origin': '*', 
                                                            'Content-Type': 'text/html; charset=UTF-8'}, \
                bytes(page['content'], 'utf-8')

    async def handle_client(self, websocket, path):
        conn_id = self.str_uuid(22) 
        self.connections[conn_id] = {'websocket': websocket,
                               'threads': {},
                               'pubkey': None}
        try:
            async for raw_message in websocket:
                message_in = json.loads(raw_message[32:])
                thread_id = raw_message[:32]
                message_out = await self.handle_message(conn_id, thread_id, message_in)

                if message_out is not None:
                    await self._send(conn_id, thread_id, message_out)
        finally:
            connection = self.connections.pop(conn_id)
            for thread_id in connection['threads']:
                await self.close_thread(conn_id, connection['threads'][thread_id])

    async def close_thread(self, conn_id, thread):
        if thread is not None:
            for service in thread['services']:
                self.services[service]['workers'].remove((conn_id, thread['thread_id']))
            for call_id in thread['calls']:
                await self.assign_call(**thread['calls'][call_id])

    async def assign_call(self, function, args=None, kwargs=None, caller_id=None, caller_thread=None, call_id=None):
        if call_id is None:
            call_id = self.str_uuid(5)
        
        call = {
            'call_id': call_id,
            'caller_id': caller_id,
            'caller_thread': caller_thread,
            'function': function,
            'args': args,
            'kwargs': kwargs
        }
        
        if self.services[function]['workers']:
            worker_id, thread_id = self.services[function]['workers'].pop()

            worker = self.connections[worker_id]
            
            worker['threads'][thread_id]['calls'][call_id] = call

            await self._send(worker_id, thread_id, {'args': args, 'kwargs': kwargs, 'call_id': call_id})
            return -1
        else:
            self.services[call['function']]['backlog'].appendleft(call)
        return call_id
    
    async def handle_message(self, conn_id, thread_id, m):
        if 'method' not in m:
            return 'MALFORMED MESSAGE: `method` not found in message'

        if m['method'] == 'AUTHENTICATE':
            if 'pubkey' not in m or 'timestamp' not in m or 'signature' not in m:
                return 'MALFORMED MESSAGE: `pubkey` or `timestamp` or `signature` not found in `AUTHENTICATE` message'
    
            if verify(m['signature'], m['timestamp'], m['pubkey']) is None and (int(m['timestamp']) > time.time()-15):
                self.connections[conn_id]['pubkey'] = m['pubkey']
                return 'OK'
            return 'Error: Bad Authentication'

        if m['method'] == 'SKIP_AUTH':
            return self.unautheticated_message

        if m['method'] == 'PUBLISH':
            if self.master_pubkeys is not None:
                if self.connections[conn_id]['pubkey'] not in self.master_pubkeys:
                    return 'UNAUTHORIZED'

            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `PUBLISH` message'
            self.services[m['function']] = {'workers': set(),
                                            'backlog': deque()}

            if 'static' in m:
                static = m['static'] if isinstance(m['static'], dict) else {'content': m['static']}
                static['function'] = m['function']
                self.static['/'+m['function']] = static
            return 'OK'
            
        if m['method'] == 'SERVE':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `OFFER` message'
            if m['function'] not in self.services:
                return 'SERVICE NOT FOUND'

            self.services[m['function']]['workers'].add((conn_id, thread_id))
            
            if thread_id not in self.connections[conn_id]['threads']:
                self.connections[conn_id]['threads'][thread_id] = {
                    'thread_id': thread_id,
                    'services': set(),
                    'calls': {}
                }
            self.connections[conn_id]['threads'][thread_id]['services'].add(m['function'])
            return len(self.services[m['function']]['workers'])

        if m['method'] == 'CALL':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `CALL` message'
            if m['function'] not in self.services:
                return 'SERVICE NOT FOUND'
            
            return await self.assign_call(m['function'], m.get('args'), m.get('kwargs'), conn_id, thread_id)

        if m['method'] == 'RETURN':
            if 'call_id' not in m:
                return 'MALFORMED MESSAGE: `call_id` not found in `RETURN` message'
            call = self.connections[conn_id]['threads'][thread_id]['calls'].pop(m['call_id'], None)

            if 'return' in m:
                if call['caller_id'] in self.connections:
                    await self._send(call['caller_id'], call['caller_thread'], m['return'])

            if self.services[call['function']]['backlog']:
                new_call = self.services[call['function']]['backlog'].pop()
                self.connections[conn_id]['threads'][thread_id]['calls'][new_call['call_id']] = new_call
                return {'args': new_call['args'], 'kwargs': new_call['kwargs'], 'call_id': new_call['call_id']}

            else:
                self.services[call['function']]['workers'].add((conn_id, thread_id))

            return len(self.services[call['function']]['backlog'])

        if m['method'] == 'LIST':
            return list(self.services.keys())

        if m['method'] == 'REMOVE':
            if self.master_pubkeys is not None:
                if self.connections[conn_id]['pubkey'] not in self.master_pubkeys:
                    return 'UNAUTHORIZED'

            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `PUBLISH` message'
            
            service = self.services.pop(m['function'], None)
            self.static.pop('/'+m['function'], None)

            if service is not None:
                for worker_id, worker_thread in service['workers']:
                    if worker_id in self.connections and m['function'] in self.connections[worker_id]['threads'][worker_thread]['services']:
                        self.connections[worker_id]['threads'][worker_thread]['services'].remove(m['function'])
                        await self._send(worker_id, worker_thread, m['function'] + ' removed')

                for call in service['backlog']:
                    if call['caller_id'] in self.connections:
                        await self._send(call['caller_id'], call['caller_thread'], m['function'] + ' removed')

            return m['function'] + ' removed'

        if m['method'] == 'CLOSE_THREAD':
            thread = self.connections[conn_id]['threads'].pop(thread_id, None)
            await self.close_thread(conn_id, thread)
            return None

        return 'Error: method not found ' + m['method']
    
    async def _send(self, conn_id, thread_id, message):
        await self.connections[conn_id]['websocket'].send(thread_id+json.dumps(message))

    async def start(self, host='localhost', port=3388):
        self.server = await websockets.serve(self.handle_client, host, port, 
                                             process_request=self.handle_incoming)
        return self
    
    async def close(self):
        return self.server.close()
