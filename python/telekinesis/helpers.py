import random

from .client import Session, Connection, Route
from .telekinesis import Telekinesis


async def authenticate(url, print_callback=print, **kwargs):
    s = Session()
    c = await Connection(s, url)

    entrypoint = Telekinesis(c.entrypoint, s)

    user = await entrypoint._call(print_callback, **kwargs)

    if not user:
        raise Exception("Failed to authenticate")

    return user
