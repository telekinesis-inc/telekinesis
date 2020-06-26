import websockets
import asyncio
import json
from uuid import uuid4
from collections import deque
import time
import http
from .common import verify, list_roles, check_min_role, decode_message, encode_message, check_function_signature

class Hub():
    def __init__(self, host='localhost', port=3388, root_pubkey=None, parent=None, peers=None, allow_origins=None):
        self.host = host
        self.port = port

        self.root_pubkey = root_pubkey

        self.static = {}
        self.connections = {}
        self.services = {}
        self.server = None
        self.allow_origins = allow_origins if allow_origins is not None else []

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
                await self._call(page['function'], args=[],
                        kwargs={kv.split('=')[0]: '='.join(kv.split('=')[1:]) for kv in query.split('&')})

            return http.HTTPStatus(page['code'] if 'code' in page else 200), \
                page['headers'] if 'headers' in page else {'Access-Control-Allow-Origin': '*', 
                                                            'Content-Type': 'text/html; charset=UTF-8'}, \
                bytes(page['content'], 'utf-8')
        
        return http.HTTPStatus(504), {}, 'UNAUTHORIZED'

    async def handle_client(self, websocket, path):

        client = ConnectionWithClient(websocket)
        thread_id, message_in = await decode_message(await websocket.recv(), client, False)
        if message_in is None:
            print('Invalid authentication')
            return
        
        pubkey, roles = self._authenticate(message_in)
        print(pubkey[102:107], thread_id, 'INITIAL AUTHENTICATE')

        if pubkey not in self.connections:
            self.connections[pubkey] = {'threads': {},
                                        'roles': roles}

        if thread_id not in self.connections[pubkey]['threads']:
            await self._create_thread(pubkey, thread_id, client)
        else:
            old_client = self.connections[pubkey]['threads'][thread_id]['client']
            old_client.threads.remove(thread_id)
            for pending_message_key in old_client.pending_messages:
                if pending_message_key[0] == thread_id:
                    pending_message = old_client.pop(pending_message_key)
                    client.pending_messages[pending_message_key] = pending_message
            client.seen_messages[0].update(old_client.seen_messages[0])
            client.seen_messages[1].update(old_client.seen_messages[1])
            client.threads.add(thread_id)

        # try:
        await self._send(pubkey, thread_id, list(roles))

        async for raw_message in websocket:
            thread_id, message_in = await decode_message(
                raw_message,
                client,
                False)

            if thread_id not in self.connections[pubkey]['threads']:
                await self._create_thread(pubkey, thread_id, client)
            client.threads.add(thread_id)

            if message_in is None:
                continue
            message_out = await self.handle_message(pubkey, thread_id, message_in)

            if message_out is not None:
                await self._send(pubkey, thread_id, message_out)
        # except websockets.exceptions.ConnectionClosedOK:
        #     print('connection disconnected OK', pubkey[102:107], len(self.connections.get(pubkey)['threads']), 'threads running')
        # except Exception as e:
        #     print(pubkey[102:107], 'connection disconnected with errors', len(self.connections.get(pubkey)['threads']), 'threads running', e)
        # finally:
        #     connection = self.connections.get(pubkey)

        #     for thread_id in client.threads:
        #         await self._pause_thread(pubkey, thread_id)

        #         connection['threads'][thread_id]['client'].websocket = None
        #         asyncio.get_event_loop().create_task(self._thread_timeout(pubkey, thread_id))

    async def handle_message(self, pubkey, thread_id, m):
        if 'method' not in m:
            return 'MALFORMED MESSAGE: `method` not found in message'

        print(pubkey[102:107], thread_id, m['method'])

        if m['method'] == 'AUTHENTICATE':
            pubkey, roles = self._authenticate(m)
            self.connections[pubkey]['roles'] = roles
            return list(roles)

        if m['method'] == 'PUBLISH':
            if 'function' not in m:
                return {'error': 'MALFORMED MESSAGE: `function` not found in `PUBLISH` message'}
            if 'signature' not in m:
                return {'error': 'MALFORMED MESSAGE: `signature` not found in `PUBLISH` message'}
            if 'replace' not in m:
                return {'error': 'MALFORMED MESSAGE: `replace` not found in `PUBLISH` message'}
            
            authorized = True
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 0)], self.connections[pubkey]['roles']):
                    authorized = False
                    
            if m['replace'] == True or m['function'] not in self.services \
            or m['signature'] != self.services[m['function']]['signature'] \
            or m['type'] != self.services[m['function']]['type']:
                if not authorized:
                    return {'error': 'UNAUTHORIZED'}
                

                if check_function_signature(m['signature']):
                    return {'error': check_function_signature(m['signature'])}

                await self._remove_service(m['function'])

                self.services[m['function']] = {'signature': m['signature'],
                                                'docstring': m['docstring'],
                                                'type': m['type'],
                                                'workers': set(),
                                                'backlog': deque(),
                                                'can_call': m.get('can_call') or [],
                                                'can_serve': m.get('can_serve') or [],
                                                'cannot_list': m.get('cannot_list') or []}

            else:
                for attribute in ['docstring', 'can_call', 'can_serve', 'cannot_list']:
                    if m.get(attribute) is not None and self.services[m['function']][attribute] != m[attribute]:
                        if not authorized:
                            return {'warning': 'UNAUTHORIZED to modify service attributes'}
                        self.services[m['function']][attribute] = m[attribute]

            if 'static' in m and m['static'] is not None:
                if authorized:
                    static = m['static'] if isinstance(m['static'], dict) else {'content': m['static']}
                    static['function'] = m['function']
                    self.static['/'+m['function']] = static
                else:
                    return {'warning': 'UNAUTHORIZED to modify service attributes'}

            return 'OK'
            
        if m['method'] == 'SERVE':
            return await self._serve(pubkey, thread_id, m)

        if m['method'] == 'GET_SERVICE':
            if 'function' not in m:
                return {'error': 'MALFORMED MESSAGE: `function` not found in `CALL` message'}
            if m['function'] not in self.services:
                return {'error': 'SERVICE NOT FOUND'}
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 2)] + self.services[m['function']]['can_call'], 
                                      self.connections[pubkey]['roles']):
                    return {'error': 'UNAUTHORIZED'}
               
            return {'signature': self.services[m['function']]['signature'], 
                    'docstring': self.services[m['function']]['docstring'],
                    'type': self.services[m['function']]['type'],
                    'can_serve': self.root_pubkey is None or check_min_role([(m['function'], 1)] + \
                                                             self.services[m['function']]['can_serve'], \
                                                             self.connections[pubkey]['roles'])}

        if m['method'] == 'CALL':
            if 'function' not in m:
                return {'error': 'MALFORMED MESSAGE: `function` not found in `CALL` message'}
            if m['function'] not in self.services:
                return {'error': 'SERVICE NOT FOUND'}
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 2)] + self.services[m['function']]['can_call'], 
                                      self.connections[pubkey]['roles']):
                    return {'error': 'UNAUTHORIZED'}
               
            return await self._call(m['function'], m.get('args'), m.get('kwargs'), pubkey, thread_id)

        if m['method'] == 'SEND':
            if 'call_id' not in m:
                return {'error: ''MALFORMED MESSAGE: `call_id` not found in `RETURN` message'}
            
            call = self.connections[pubkey]['threads'][thread_id]['call_requests'][m['call_id']]

            if 'worker_id' in call:
                await self._send(call['worker_id'], call['worker_thread'], m)
            else:
                call['additional_data'].append(m)
            return

        if m['method'] == 'RETURN':
            if 'call_id' not in m:
                return {'error': 'MALFORMED MESSAGE: `call_id` not found in `RETURN` message'}

            if 'call_return' in m or 'error' in m:
                call = self.connections[pubkey]['threads'][thread_id]['calls_processing'].pop(m['call_id'], None)
                if call['caller_id'] in self.connections:
                    await self._send(call['caller_id'], call['caller_thread'], m)

                    if call['caller_id'] in self.connections and call['caller_thread'] in self.connections[call['caller_id']]['threads']:
                        self.connections[call['caller_id']]['threads'][call['caller_thread']]['call_requests'].pop(call['call_id'])

            else:
                call = self.connections[pubkey]['threads'][thread_id]['calls_processing'][m['call_id']]
                await self._send(call['caller_id'], call['caller_thread'], m)
            
            return

        if m['method'] == 'LIST':
            # TODO implement cannot_list
            return [x for x in self.services.keys() if self.root_pubkey is None or \
                          check_min_role([(x, 2)] + self.services[x]['can_call'], self.connections[pubkey]['roles'])]

        if m['method'] == 'REMOVE':
            if self.root_pubkey is not None:
                if not check_min_role([(m['function'], 0)], 
                                      self.connections[pubkey]['roles']):
                    return 'UNAUTHORIZED'

            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `PUBLISH` message'
            
            return await self._remove_service(m['function'])

        if m['method'] == 'RECOVER_THREADS':
            lst = []
            for rec_thread_id in m['threads']:
                if rec_thread_id in self.connections[pubkey]['threads']:
                    lst.append(rec_thread_id)
                    rec_thread = self.connections[pubkey]['threads'][rec_thread_id]
                    rec_thread['client'] = self.connections[pubkey]['threads'][thread_id]['client']
                    await self._resume_thread(pubkey, rec_thread_id)
            return {'recovered_threads': lst}

        if m['method'] == 'CLOSE_THREAD':
            await self._close_thread(pubkey, thread_id)
            return None

        if m['method'] == 'ECHO':
            return m

        return {'error': 'method not found ' + m['method']}
    
    async def _create_thread(self, pubkey, thread_id, client):
        # print(pubkey[102:107], thread_id, 'create thread')
        self.connections[pubkey]['threads'][thread_id] = {
            'thread_id': thread_id,
            'chunks': {},
            'services': set(),
            'paused_services': [],
            'call_requests': {},
            'calls_processing': {},
            'message_buffer': [],
            'client': client}

    async def _pause_thread(self, pubkey, thread_id):
        # print(pubkey[102:107], thread_id, 'pause thread')

        thread = self.connections[pubkey]['threads'].get(thread_id)
        if thread is not None:
            for service in thread['services']:
                x = (pubkey, thread['thread_id'])
                if x in self.services[service]['workers']: 
                    thread['paused_services'].append(service)
                    self.services[service]['workers'].remove(x)

    async def _resume_thread(self, pubkey, thread_id):
        # print(pubkey[102:107], thread_id, 'resume thread')

        thread = self.connections[pubkey]['threads'].get(thread_id)
        if thread is not None:
            for old_message in thread['message_buffer']:
                await self._send(pubkey, thread_id, old_message)
            for service in thread['paused_services']:
                await self._serve(pubkey, thread_id, {'function': service})
            thread['paused_services'] = []

    async def _close_thread(self, pubkey, thread_id):
        # print(pubkey[102:107], thread_id, 'close thread')
        thread = self.connections[pubkey]['threads'].pop(thread_id, None)
        if thread is not None:
            if thread['client'] is not None:
                thread['client'].threads.remove(thread_id)
            for call in thread['calls_processing'].values():
                if 'caller_id' in call and 'caller_thread' in call:
                    if call['caller_id'] in self.connections and call['caller_thread'] in self.connections[call['caller_id']]['threads']:
                        await self._send(call['caller_id'], call['caller_thread'], {'error': 'Service Disconnected'})
                        self.connections[call['caller_id']]['threads'][call['caller_thread']]['call_requests'].pop(call['call_id'])
                    # print('cancelling', call)
            for call in thread['call_requests'].values():
                if call['call_id'] in self.services[call['function']]['backlog']:
                    self.services[call['function']]['backlog'].remove(call['call_id'])
                if 'worker_id' in call and 'worker_thread' in call:
                    if call['worker_id'] in self.connections and call['worker_thread'] in self.connections[call['worker_id']]['threads']:
                        if call['call_id'] in self.connections[call['worker_id']]['threads'][call['worker_thread']]['calls_processing']:
                            await self._send(call['worker_id'], call['worker_thread'], {'call_id': call['call_id'], '_close': True})
                            self.connections[call['worker_id']]['threads'][call['worker_thread']]['calls_processing'].pop(call['call_id'], None)
            # if not thread['client'].threads:
            #     pm = thread['client'].pending_messages
            #     for m in pm.copy():
            #         await asyncio.wait_for(pm[m].wait(), 1)

    async def _thread_timeout(self, pubkey, thread_id, timeout=90):
        await asyncio.sleep(timeout)
        # print(pubkey[102:107], thread_id, 'thread timeout over')

        if pubkey in self.connections and thread_id in self.connections[pubkey]['threads']:
            if self.connections[pubkey]['threads'][thread_id]['client'].websocket is None:
                await self._close_thread(pubkey, thread_id)

                if not self.connections[pubkey]['threads']:
                    self.connections.pop(pubkey)

    def _str_uuid(self, n, d=None):
        while True:
            i = uuid4().hex[:n]
            if d is None or i not in d:
                return i
    
    def _authenticate(self, m):
        if 'pubkey' not in m or 'timestamp' not in m or 'signature' not in m:
            raise Exception('MALFORMED MESSAGE: `pubkey` or `timestamp` or `signature` not found in `AUTHENTICATE` message')

        if verify(m['signature'], m['timestamp'], m['pubkey']) is None and (int(m['timestamp']) > time.time()-15):
            pubkey = m['pubkey']

            roles = list_roles(self.root_pubkey, m['pubkey'], *m['role_certificates']) if 'role_certificates' in m else set()
            return pubkey, roles
        raise Exception('Bad Authentication')

    async def _call(self, function, args=None, kwargs=None, caller_id=None, caller_thread=None, call_id=None):
        if call_id is None:
            call_id = self._str_uuid(10)
        
        call = {
            'call_id': call_id,
            'caller_id': caller_id,
            'caller_thread': caller_thread,
            'function': function,
            'args': args,
            'kwargs': kwargs,
            'additional_data': []
        }

        if caller_id in self.connections and caller_thread in self.connections[caller_id]['threads']:
            self.connections[caller_id]['threads'][caller_thread]['call_requests'][call_id] = call
            await self._send(caller_id, caller_thread, {'call_id': call_id})
        
        if self.services[function]['workers']:
            worker_id, thread_id = self.services[function]['workers'].pop()

            await self._assign(worker_id, thread_id, call)
        else:
            self.services[call['function']]['backlog'].appendleft(call)
        return

    async def _assign(self, worker_id, worker_thread, call):
        call['worker_id'] = worker_id
        call['worker_thread'] = worker_thread

        worker = self.connections[worker_id]
        
        worker['threads'][worker_thread]['calls_processing'][call['call_id']] = call

        await self._send(worker_id, worker_thread, {'call': {'args': call['args'], 
                                                                'kwargs': call['kwargs'], 
                                                                'call_id': call['call_id'], 
                                                                'caller_pubkey': call['caller_id']}})

    async def _serve(self, pubkey, thread_id, m):
        if 'function' not in m:
            return {'error': 'MALFORMED MESSAGE: `function` not found in `SERVE` message'}
        if m['function'] not in self.services:
            return {'error': 'SERVICE NOT FOUND'}
        if self.root_pubkey is not None:
            if not check_min_role([(m['function'], 1)] + self.services[m['function']]['can_serve'], 
                                    self.connections[pubkey]['roles']):
                return {'error': 'UNAUTHORIZED'}

        self.connections[pubkey]['threads'][thread_id]['services'].add(m['function'])

        if self.services[m['function']]['backlog']:
            new_call = self.services[m['function']]['backlog'].pop()

            await self._assign(pubkey, thread_id, new_call)
            
            return

        self.services[m['function']]['workers'].add((pubkey, thread_id))
        
        return #{'print': str(len(self.services[m['function']]['workers'])) + ' total workers serving '+ m['function']}

    async def _remove_service(self, function):
            service = self.services.pop(function, None)
            self.static.pop('/'+function, None)

            if service is not None:
                for worker_id, worker_thread in service['workers']:
                    if worker_id in self.connections and function in self.connections[worker_id]['threads'][worker_thread]['services']:
                        self.connections[worker_id]['threads'][worker_thread]['services'].remove(function)
                        await self._send(worker_id, worker_thread, {'service_removed': function + ' removed'})

                for call in service['backlog']:
                    if call['caller_id'] in self.connections:
                        await self._send(call['caller_id'], call['caller_thread'], {'error': function + ' removed'})

            return 'Done' #m['function'] + ' removed'
    async def _send(self, pubkey, thread_id, message):
        # print(pubkey[102:107], thread_id, '>>>>>')
        thread = self.connections[pubkey]['threads'][thread_id]
        if thread['client'].websocket is None:
            thread['message_buffer'].append(message)
        else:
            await encode_message(thread_id, message, thread['client'])

    async def start(self, **kwargs):
        self.server = await websockets.serve(self.handle_client, self.host, self.port, 
                                             process_request=self.handle_incoming,
                                             **kwargs)
        return self
    
    async def close(self):
        # for pk in self.connections:
        #     for t in self.connections[pk]['threads']:
        #         await self._close_thread(pk, t)

        self.server.close()
        await self.server.close_task
    
    async def __aenter__(self):
        return await self.start()
    
    async def __aexit__(self, _, __, ___):
        return await self.close()

class ConnectionWithClient:
    def __init__(self, websocket):
        self.websocket = websocket
        self.pending_messages = {}
        self.seen_messages = (set(), set())
        self.threads = set()
        self._chunks = {}