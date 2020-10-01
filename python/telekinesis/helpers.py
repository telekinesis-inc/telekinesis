import random
import aiohttp

from .client import Session, Connection, Route
from .telekinesis import Telekinesis

async def authenticate(url, print_callback=print, **kwargs):

    url = url if url[-5:] == '.json' else url.rstrip('/') + '/brokers.json'
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            brokers = await resp.json()

    bid, b = random.choice(list(brokers.items()))

    s = Session()
    c = Connection(s, b['url'])

    await c.lock.wait()
    assert bid == c.broker_id
    entrypoint = Telekinesis(Route(**b['entrypoint']), s)

    user = await entrypoint(print_callback, **kwargs)

    if not user:
        raise Exception('Failed to authenticate')

    return user