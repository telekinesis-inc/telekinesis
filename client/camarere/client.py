from uuid import uuid4
import requests
import inspect
import json
from multiprocessing import Process

class Cmrr():
    def __init__(self, url, user_id=None, token=None):
        self.url = url + ('/' if url[-1] != '/' else '')
        if user_id is None:
            user_data = requests.get(self.url+'tmp_user').json()
            user_id = user_data['user_id']
            token = user_data['token']
        
            print('Using temporary user_id="'+user_id+'", token="'+ token+'"')
            print('Head over to https://cmrr.es to create a permanent user.')
        
        self.user_id = user_id
        self.token = token

        if self._get('check_token/'+self.user_id).content != b'OK':
            print(self._get('check_token/'+self.user_id).content)
            raise Exception('Authorization Error') 

    def _get(self, endpoint, args=None):
        return requests.get(self.url + endpoint, 
                            args, 
                            headers = {'Token': self.token})

    def serve(self, func, name=None, processors=1):
        if name is None:
            BASE = 'QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm1234567890'
            name = ''.join([BASE[uuid4().int//(len(BASE)**i)%len(BASE)] 
                            for i in range(4)])
        params = dict(inspect.signature(func).parameters)

        strp = '?'+'&'.join([p+'='+(str(params[p].default) 
                                    if params[p].default != inspect._empty 
                                    else '')
                             for p in params])
        print('serving', func.__name__, 'at', 
              self.url + 'call/' + self.user_id + '/' + name + strp)
        def target():
            while True:
                request = self._get('serve/'+self.user_id+'/'+name)
                if request.status_code == 504:
                    continue
                self._get('return/'+request.json()['call_id']+'/'+\
                        str(func(**json.loads(request.json()['kwargs']))))
        if processors == 0:
            target()
        else:
            for _ in range(processors):
                Process(target=target).start()
    
    def call(self, f_name, kwargs=None, user_id=None):
        if user_id is None:
            user_id = self.user_id
        return str(self._get('call/'+user_id+'/'+f_name, kwargs).content, 'utf-8')