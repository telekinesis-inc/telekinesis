import websockets
# import asyncio
import json
from uuid import uuid4
from collections import deque
import time
import http
from .common import verify, list_roles, check_min_role, decode_message, encode_message

class Hub():
    def __init__(self, root_pubkey=None, parent=None, peers=None, allow_origins=None):
        self.root_pubkey = root_pubkey

        self.static = {}
        self.connections = {}
        self.services = {}
        self.server = None
        self.allow_origins = allow_origins if allow_origins is not None else []

    def _str_uuid(self, n, d=None):
        while True:
            i = uuid4().hex[:n]
            if d is None or i not in d:
                return i
    
    async def handle_incoming(self, path, headers):
        if path[:4] == '/ws/':
            if headers.get('Origin') is None or headers.get('Origin') in self.allow_origins:
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
        
        return http.HTTPStatus(504), {}, 'UNAUTHORIZED'

    async def handle_client(self, websocket, path):

        thread_id, message_in = decode_message(await websocket.recv(), None)
        
        pubkey, roles = self.authenticate(message_in)

        print('new connection', pubkey[100:105])

        self.connections[pubkey] = {'websocket': websocket,
                                    'threads': {},
                                    'roles': roles}
        
        try:
            await self._send(pubkey, thread_id, list(roles))

            async for raw_message in websocket:
                thread_id, message_in = decode_message(raw_message, self.connections[pubkey]['threads'])
                if message_in is None:
                    continue
                message_out = await self.handle_message(pubkey, thread_id, message_in)

                if message_out is not None:
                    await self._send(pubkey, thread_id, message_out)
        finally:
            connection = self.connections.pop(pubkey)
            print('connection disconnected', pubkey[100:105])
            for thread_id in connection['threads']:
                await self.close_thread(pubkey, connection['threads'][thread_id])

    async def close_thread(self, pubkey, thread):
        if thread is not None:
            for service in thread['services']:
                self.services[service]['workers'].remove((pubkey, thread['thread_id']))
            for call_id in thread['calls']:
                await self.assign_call(**thread['calls'][call_id])

    async def assign_call(self, function, args=None, kwargs=None, caller_id=None, caller_thread=None, call_id=None):
        if call_id is None:
            call_id = self._str_uuid(10)
        
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
    
    def authenticate(self, m):
        if 'pubkey' not in m or 'timestamp' not in m or 'signature' not in m:
            raise Exception('MALFORMED MESSAGE: `pubkey` or `timestamp` or `signature` not found in `AUTHENTICATE` message')

        if verify(m['signature'], m['timestamp'], m['pubkey']) is None and (int(m['timestamp']) > time.time()-15):
            pubkey = m['pubkey']

            roles = list_roles(self.root_pubkey, m['pubkey'], *m['role_certificates']) if 'role_certificates' in m else set()
            return pubkey, roles
        raise Exception('Bad Authentication')

    async def handle_message(self, pubkey, thread_id, m):

        if 'method' not in m:
            return 'MALFORMED MESSAGE: `method` not found in message'

        if m['method'] == 'AUTHENTICATE':
            pubkey, roles = self.authenticate(m)
            self.connections[pubkey]['roles'] = roles
            return list(roles)

        if m['method'] == 'PUBLISH':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `PUBLISH` message'
            
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 0)], self.connections[pubkey]['roles']):
                    return 'UNAUTHORIZED'

            self.services[m['function']] = {'workers': set(),
                                            'backlog': deque(),
                                            'can_call': m.get('can_call') or [],
                                            'can_serve': m.get('can_serve') or [],
                                            'cannot_list': m.get('cannot_list') or []}

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
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 1)] + self.services[m['function']]['can_serve'], 
                                      self.connections[pubkey]['roles']):
                    return 'UNAUTHORIZED'
            self.connections[pubkey]['threads'][thread_id]['services'].add(m['function'])

            if self.services[m['function']]['backlog']:
                await self._send(pubkey, thread_id, -1)
                new_call = self.services[m['function']]['backlog'].pop()
                self.connections[pubkey]['threads'][thread_id]['calls'][new_call['call_id']] = new_call
                return {'args': new_call['args'], 'kwargs': new_call['kwargs'], 'call_id': new_call['call_id']}

            self.services[m['function']]['workers'].add((pubkey, thread_id))
            
            return len(self.services[m['function']]['workers'])

        if m['method'] == 'CALL':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `CALL` message'
            if m['function'] not in self.services:
                return 'SERVICE NOT FOUND'
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 2)] + self.services[m['function']]['can_serve'], 
                                      self.connections[pubkey]['roles']):
                    return 'UNAUTHORIZED'
               
            return await self.assign_call(m['function'], m.get('args'), m.get('kwargs'), pubkey, thread_id)

        if m['method'] == 'RETURN':
            if 'call_id' not in m:
                return 'MALFORMED MESSAGE: `call_id` not found in `RETURN` message'
            call = self.connections[pubkey]['threads'][thread_id]['calls'].pop(m['call_id'], None)

            if 'return' in m:
                if call['caller_id'] in self.connections:
                    await self._send(call['caller_id'], call['caller_thread'], m['return'])

            if self.services[call['function']]['backlog']:
                new_call = self.services[call['function']]['backlog'].pop()
                self.connections[pubkey]['threads'][thread_id]['calls'][new_call['call_id']] = new_call
                return {'args': new_call['args'], 'kwargs': new_call['kwargs'], 'call_id': new_call['call_id']}

            else:
                self.services[call['function']]['workers'].add((pubkey, thread_id))

            return len(self.services[call['function']]['backlog'])

        if m['method'] == 'LIST':
            return list(self.services.keys())

        if m['method'] == 'REMOVE':
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 0)], 
                                      self.connections[pubkey]['roles']):
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
            thread = self.connections[pubkey]['threads'].pop(thread_id, None)
            await self.close_thread(pubkey, thread)
            return None

        if m['method'] == 'ECHO':
            return m

        return 'Error: method not found ' + m['method']
    
    async def _send(self, pubkey, thread_id, message):
        await encode_message(thread_id, message, self.connections[pubkey]['websocket'])

    async def start(self, host='localhost', port=3388, **kwargs):
        self.server = await websockets.serve(self.handle_client, host, port, 
                                             process_request=self.handle_incoming,
                                             **kwargs)
        return self
    
    async def close(self):
        return self.server.close()
