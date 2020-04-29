from uuid import uuid4
import requests
import json

class Cmrr():
    def __init__(self, url, user_id=None, token=None):
        self.url = url + ('/' if url[-1] != '/' else '')
        if user_id is None:
            user_data = requests.get(self.url+'tmp_user').json()
            user_id = user_data['user_id']
            token = user_data['token']
        
            print('Using temporary user:', user_id, 'token:', token)
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

    def serve(self, func, name=None):
        if name is None:
            BASE = 'QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm1234567890'
            name = ''.join([BASE[uuid4().int//(len(BASE)**i)%len(BASE)] 
                            for i in range(4)])
        print('serving', func.__name__, 'at', 
              self.url + 'call/' + self.user_id + '/' + name)
        while True:
            request = self._get('serve/'+self.user_id+'/'+name).json()
            print('request', request)
            self._get('return/'+request['call_id']+'/'+\
                      str(func(**json.loads(request['kwargs']))))