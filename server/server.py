from redis import Redis
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from uuid import uuid4
import json

r = Redis()

app = Flask(__name__)
cors = CORS(app, resources={"/*": {"origins": "*"}})

with open('landing.html', 'r') as f:
    LANDING = f.read()

@app.route('/', methods=['GET'])
def _landing():
    return LANDING

@app.route('/tmp_user', methods=['GET'])
def _tmp_user():
    BASE = 'QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm1234567890'
    str_uuid = lambda n: ''.join([BASE[uuid4().int//(len(BASE)**i)%len(BASE)] 
                                 for i in range(n)])
    user_id = str_uuid(9)
    token = str_uuid(19)

    p = r.pipeline()
    p.hset('user:'+user_id, 'token', token)
    p.expire('user:'+user_id, 300)
    p.execute()

    return jsonify({'user_id': user_id, 'token': token})

@app.route('/check_token/<user_id>', methods=['GET'])
def _check_token(user_id):
    token = str(r.hget('user:'+user_id, 'token'), 'utf-8')
    return 'OK' if (token == request.headers.get('Token')) else 'UNAUTHORIZED'

@app.route('/serve/<user_id>/<service_id>', methods=['GET'])
def _serve(user_id, service_id):
    token = str(r.hget('user:'+user_id, 'token'), 'utf-8')
    
    if token != request.headers.get('Token'):
        return Response('Authentication Error', 401)

    route = user_id +'/'+ service_id
    
    print('serving:', route)
    last = {('stream:'+ route): 0}
    
    while True:
        last_id = r.xread(last, None, 0)[0][1][-1][0]
        for queue in ['backlog:', 'working:']:
            call_id = r.rpoplpush(queue + route,
                            'working:'+ route)
            if call_id is not None:
                return jsonify({
                    'call_id': str(call_id, 'utf-8'),
                    'kwargs': str(r.hget('args', call_id), 'utf-8')
                })
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

        p = r.pipeline()
        
        p.hset('calls', call_id, route)
        p.hset('args', call_id, args)
        
        p.lpush('backlog:'+ route, call_id)

        p.xadd('stream:'+ route, {'call_id': call_id, 'kwargs': args})
        p.execute()

        
        return str(next(listener)['data'], 'utf-8')
        
@app.route('/return/<call_id>/<value>', methods=['GET'])
def _return(call_id, value):
    route = r.hget('calls', call_id)
    r.lrem(b'working:'+ route, 1, call_id)
    r.hdel('calls', call_id)
    return str(r.publish('call:'+call_id, value))

if __name__ == '__main__':
    app.run('0.0.0.0', threaded=True)
