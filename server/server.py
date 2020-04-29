from redis import Redis
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from uuid import uuid4
import json
import time

r = Redis()

app = Flask(__name__)
cors = CORS(app, resources={"/*": {"origins": "*"}})

with open('server/landing.html', 'r') as f:
    LANDING = f.read()

@app.route('/', methods=['GET'])
def _landing():
    return LANDING

@app.route('/tmp_user', methods=['GET'])
def _tmp_user():
    BASE = 'QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm1234567890'
    str_uuid = lambda n: ''.join([BASE[uuid4().int//(len(BASE)**i)%len(BASE)] 
                                 for i in range(n)])
    user = str_uuid(9)
    token = str_uuid(19)

    r.hset('users', user, token)
    return jsonify({'user_id': user, 'token': token})

@app.route('/check_token/<user_id>', methods=['GET'])
def _check_token(user_id):
    token = str(r.hget('users', user_id), 'utf-8')
    return 'OK' if (token == request.headers.get('Token')) else 'UNAUTHORIZED'

@app.route('/serve/<user_id>/<service_id>', methods=['GET'])
def _serve(user_id, service_id):
    token = str(r.hget('users', user_id), 'utf-8')
    
    if token != request.headers.get('Token'):
        return Response('Authentication Error', 401)

    route = user_id +'/'+ service_id
    
    print('serving:', route)
    last = {('stream:'+ route): 0}
    
    while True:
        last_id = r.xread(last, None, 0)[0][1][-1][0]
        print(last_id)
        for queue in ['backlog:', 'backlog:', 'working:']:
            call_id = r.rpoplpush(queue + route,
                               'working:'+ route)
            if call_id is not None:
                return jsonify({
                    'call_id': str(call_id, 'utf-8'),
                    'kwargs': str(r.hget('args', call_id), 'utf-8')
                })
            time.sleep(2)
        last['stream:' + route] = last_id

@app.route('/call/<user_id>/<service_id>', methods=['GET'])
def _call(user_id, service_id):
    route = user_id +'/'+ service_id
    
    print('calling:', route)
    
    with r.pubsub() as pb:
        call_id = uuid4().hex
        pb.subscribe('call:'+call_id)
        listener = pb.listen()
        next(listener)
        
        args = json.dumps(request.args.to_dict())
        
        r.hset('calls', call_id, route)
        r.hset('args', call_id, args)
        
        r.lpush('backlog:'+ route, call_id)
        r.xadd('stream:'+ route, {'call_id': call_id, 'kwargs': args})
        
        return str(next(listener)['data'], 'utf-8')
        
@app.route('/return/<call_id>/<value>', methods=['GET'])
def _return(call_id, value):
    route = r.hget('calls', call_id)
    r.lrem(b'working:'+ route, 1, call_id)
    r.hdel('calls', call_id)
    return str(r.publish('call:'+call_id, value))

app.run()