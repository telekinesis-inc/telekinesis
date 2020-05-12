from camarere import Server
import asyncio

PUBKEY = ''
with open('PUBLIC.pub', 'r') as f:
    PUBKEY = f.read()

server = Server(PUBKEY, unautheticated_message='Head over to https://cmrr.es to create an account.').serve()

loop = asyncio.get_event_loop()
loop.run_until_complete(server)
loop.run_forever()


