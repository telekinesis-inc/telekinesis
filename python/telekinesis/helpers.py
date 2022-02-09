import re
from typing import ChainMap

from .client import Channel, Session, Connection
from .telekinesis import Telekinesis


def authenticate(url="ws://localhost:8776", session_key=None, print_callback=print, **kwargs):

    user = Entrypoint(url, session_key).authenticate(print_callback, **kwargs)._subscribe()

    return user


def Entrypoint(url="ws://localhost:8776", session_key=None, **kwargs):
    s = Session(session_key)

    if re.sub(r"(?![\w\d]+:\/\/[\w\d.]+):[\d]+", "", url) == url:
        i = len(re.findall(r"[\w\d]+:\/\/[\w\d.]+", url)[0])
        url = url[:i] + ":8776" + url[i:]

    c = Connection(s, url)

    async def await_entrypoint():
        await c
        return c.entrypoint

    return Telekinesis(await_entrypoint(), s, **kwargs)


async def create_entrypoint(target, url="ws://localhost:8776", session_key=None, channel_key=None, is_public=True, **kwargs):
    """
    Returns a tuple (tk._channel.route, tk) with tk being a Telekinesis object pointing to `target`.
    """
    s = Session(session_key)

    if re.sub(r"(?![\w\d]+:\/\/[\w\d.]+):[\d]+", "", url) == url:
        i = len(re.findall(r"[\w\d]+:\/\/[\w\d.]+", url)[0])
        url = url[:i] + ":8776" + url[i:]

    c = await Connection(s, url)

    tk = Telekinesis(target, s, **kwargs)
    tk._channel = await Channel(s, channel_key, is_public=is_public).listen()
    tk._channel.telekinesis = tk

    return tk._channel.route, tk
