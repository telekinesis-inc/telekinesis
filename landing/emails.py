from camarere import Server
from datetime import datetime
import asyncio

async def main(): 
    def log_email(*args, **kwargs):
        print('email', args, kwargs)
        with open('../../email.log', 'a') as f:
            f.write(' '.join(str(x) for x in (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S\n'), args, kwargs)))
    
    c = []
    while True:
        try:
            c = await Server(private_key_path='PRIVATE.pem').connect()

            await c.publish(log_email, 'register_email', 'Thank you for registering!')
            await c.supply(log_email, 'register_email')
        except Exception as e:
            print(e)
            await asyncio.sleep(1)
        finally:
            if c:
                await c.close()

asyncio.run(main())