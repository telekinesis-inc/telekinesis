import websockets
# import asyncio
import json
from uuid import uuid4
from collections import deque
import time
import http
from .encryption import verify

async def default_check_credentials(username, password): # 15 second window signed timestamp
    if username:
        timestamp = password[:10]
        signature = password[10:]+'='

        if verify(signature, timestamp, username+'==') is None and (int(timestamp) > time.time()-15):
            return True
        return False
    return True

class Server():
    def __init__(self, master_pubkeys=None, parent=None, peers=None, path_privkey=None):
        self.master_pubkeys = master_pubkeys if (isinstance(master_pubkeys, list) 
                                                 or master_pubkeys is None ) \
                                             else [master_pubkeys]

        self.static = {}
        self.clients = {}
        self.services = {}
        self.server = None

    def str_uuid(self, n):# TODO avoid collissions
        BASE = 'QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm1234567890'
        return ''.join([BASE[uuid4().int//(len(BASE)**i)%len(BASE)] 
                        for i in range(n)])
    
    async def handle_incoming(self, path, headers):
        route = path.split('?')[0]
        query = '?'.join(path.split('?')[1:])
        if route in self.static:
            page = self.static[route]
            if 'function' in page:
                await self.assign({'function': page['function'], 
                                   'call_id': self.str_uuid(3),
                                   'query': query})
            return page['code'], page['headers'], page['content']
        return None

    async def handle_client(self, websocket, path):
        i = self.str_uuid(5) 
        self.clients[i] = {'websocket': websocket,
                           'services': set(),
                           'calls': {},
                           'pubkey': websocket.username+'=='}
        try:
            async for raw_message in websocket:
                await websocket.send(json.dumps(await self.handle_message(i, json.loads(raw_message))))
        finally:
            c = self.clients.pop(i)
            for service in c['services']:
                self.services[service]['workers'].remove(i)
            for call_id in c['calls']:
                self.assign(c['calls'][call_id])

    async def assign(self, call):
        if self.services[call['function']]['workers']:
            worker = self.services[call['function']]['workers'].pop()
            self.clients[worker]['calls'][call['call_id']] = call
            await self.clients[worker]['websocket'].send(json.dumps(call))
            return worker

        self.services[call['function']]['backlog'].appendleft(call)
        return len(self.services[call['function']]['backlog'])

    async def handle_message(self, i, m):
        if 'method' not in m:
            return 'MALFORMED MESSAGE: `method` not found in message'

        if m['method'] == 'PUBLISH':
            if self.master_pubkeys is not None:
                if self.clients[i]['pubkey'] not in self.master_pubkeys:
                    return 'UNAUTHORIZED'

            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `PUBLISH` message'
            self.services[m['function']] = {'workers': set(),
                                            'backlog': deque()}
            return 'OK'
            
        if m['method'] == 'SUPPLY':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `OFFER` message'
            if m['function'] not in self.services:
                return 'SERVICE NOT FOUND'

            self.services[m['function']]['workers'].add(i)  
            self.clients[i]['services'].add(m['function'])
            return len(self.services[m['function']]['workers'])

        if m['method'] == 'CALL':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `CALL` message'
            if m['function'] not in self.services:
                return 'SERVICE NOT FOUND'
            m['caller'] = i
            m['call_id'] = self.str_uuid(3)
            
            return await self.assign(m)


        if m['method'] == 'RETURN':
            if 'function' not in m:
                return 'MALFORMED MESSAGE: `function` not found in `RETURN` message'
            if 'return' in m:
                if 'caller' not in m:
                    return 'MALFROMED MESSAGE: `caller` not found in `RETURN` message with `return`'
                if m['caller'] in self.clients:
                    await self.clients[m['caller']]['websocket'].send(json.dumps(m['return']))
            self.clients[i]['calls'].pop(m['call_id'], None)
            if self.services[m['function']]['backlog']:
                return self.services[m['function']]['backlog'].pop()
            self.services[m['function']]['workers'].add(i)

            return len(self.services[m['function']]['backlog'])

    async def serve(self, host='localhost', port=3388, check_credentials=None):
        if check_credentials is None:
            check_credentials = default_check_credentials
        self.server = await websockets.serve(self.handle_client, host, port, 
                                             process_request=self.handle_incoming,
                                             create_protocol=websockets.basic_auth_protocol_factory(
                                                 'camarere',
                                                 check_credentials=check_credentials
                                             ))
        return self.server
    
    async def close(self):
        return self.server.close()