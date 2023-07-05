import re
import asyncio

from .client import Channel, Session, Connection
from .telekinesis import Telekinesis


async def authenticate(url_or_entrypoint="ws://localhost:8776", session_key=None, print_callback=print, **kwargs):
    """
        Connect to a telekinesis server and call the authenticate method at the entrypoint. This method should take at least one 
        paramater named print_callback, that is used to guide the user into authenticating.

        Important: This authenticate method should first call the print_callback (it's first parameter) with the client's session public_key it is seeing, to make a
        man-in-the-middle attack harder. Additionally, it is encouraged that the authentication process makes it easy for users to check their
        session's public key.
        
        url_or_entrypoint: string | Telekinesis- url of the telekinesis Broker. example: "wss://payper.run" or Entrypoint object

        session_key: string | tk.Session | None - session private key used to identify yourself. If a url is provided in the url_or_entrypoint parameter and
        no session is provided, a new one will be created.

        print_callback: function - Callback generally used to guide the user through the authentication process. This is wrapped in a function that first checks
        the public key sent by the Entrypoint's authentication method against its own session public key (as explained by the `Important` note above), then proceeds to forward messages.

        **kwargs: additional arguments the broker's authenticator may implement
    """

    if isinstance(url_or_entrypoint , str):
        entrypoint = Entrypoint(url_or_entrypoint, session_key)
    else:
        entrypoint = url_or_entrypoint
    
    shared_data = {'session_key_was_checked': False}

    async def wrapped_print_callback(*x):
        if not shared_data['session_key_was_checked']:
            if not x[0] == entrypoint._session.session_key.public_serial():
                raise PermissionError("Public keys don't match")
            shared_data['session_key_was_checked'] = True
            insert_spaces = lambda s, d=6: ' '.join(s[i:i+d] for i in range(0, len(s), d))

            x = ['Your session public key is:\n' + insert_spaces(x[0]), *x[1:]]
            if not print_callback: 
                return
        if isinstance(print_callback, Telekinesis) or asyncio.iscoroutinefunction(print_callback):
            await print_callback(*x)
        else:
            print_callback(*x)
        return print_callback
        
    user = await entrypoint.authenticate(wrapped_print_callback, **kwargs)._subscribe()

    if shared_data['session_key_was_checked']:
        return user
    
    raise PermissionError('Session public key was not checked')


def Entrypoint(url="ws://localhost:8776", session_key=None, **kwargs):
    """
        Connect to a telekinesis server and connect to the entrypoint object
        
        url: string - url of the telekinesis Broker. example: "wss://telekinesis.cloud"

        session_key: string | tk.Session | None - session private key used to identify yourself

        **kwargs: additional arguments passed to the Telekinesis init
    """
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
