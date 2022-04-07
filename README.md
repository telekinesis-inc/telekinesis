# Just-in-time SDKs

## Why?

I just love to autocomplete my way out of any coding challenge on a REPL. I particularly love how in Jupyter notebooks for Python I can `Shift+Tab` on any function or object and get the function signature and docstrings right in front of me.

When you have a good SDK for a service you barely have to `Alt+Tab` to a documentation's page, all the relevant information you need is easily accessible on your REPL

The problem with SDKs is: somebody has to develop and maintain them. Moreover, if you look at the bigger picture of a client consuming a service from a server, it currently looks something like:
```
  Client UX      fetch logic      http requests    API server          Service
    logic   <--->   or SDK      <--------------->    logic     <--->    logic
 (value add)    (requires dev)                    (requires dev)     (value add)
```
Telekinesis aims to standardize the communication logic, so that, from a development perspective the picture would look more like: 
```
  Client UX                     telekinesis                            Service
    logic   <------------------------------------------------------>    logic
 (value add)                                                         (value add)
```
Under the hood, telekinesis communication is based on WebSocket clients on both the client and service sides, with WebSocket servers in between acting as message brokers. Keeping the client and service sides symmetrical opens a lot of possibilities.

## Example

Note: you can follow along on IPython or on a Jupyter notebook, you just need to `pip install telekinesis` first.

```python
# On IPython or a Jupyter notebook

import telekinesis as tk
import pandas # For example

# First let's set up a local telekinesis network
broker = await tk.Broker().serve() # That's it

# Let's put pandas behind a passkey check so it's not exposed to 
# every tab on your web browser 
def passkey_check(submitted_passkey):
    PASSKEY = '<write a random passkey here>'
    if submitted_passkey == PASSKEY:
        return pandas
    raise PermissionError()

# Now, let's create an SDK for the whole pandas module
broker.entrypoint, _ = await tk.create_entrypoint(passkey_check) # Done!
```

Now, for example, we can create and manipulate pandas DataFrames from javascript in a web browser. All you need to do is to either create or navigate to a webpage that has the `telekinesis-js` npm package installed - like (shameless plug) https://www.telekinesis.cloud - and fire up the JS console.

```javascript
// On a JS console, at a website with telekinesis-js installed
// (On www.telekinesis.cloud window.TK points to the telekinesis library)

entrypoint = await new TK.Entrypoint() // Connect to our entrypoint

pandas = await entrypoint('<your passkey>') // Let's get to our pandas sdk :)

df = await pandas.DataFrame() // Freedom, so much!

await df.to_ // Try autocomplete!

// Telekinesis also bundles function signatures and docstrings
console.log(df.to_dict.__signature__)
console.log(df.to_dict,__doc__)
```

## Encoding logic

Every time you pass data between a client and a service (like when you're passing an argument to a function or when that function returns something), that data has to be encoded. To make it easy to use, `telekinesis` always follows the same logic:

* Primitives or built in types (`str`, `bytes`, `int`, `float`, `bool`, `NoneType`, `range` and `slice`) are sent directly (meaning they'll be encoded using `bson` on one side and decoded on the other)
* Collections (`dict`, `list` and `set`) are encoded recursively
* Telekinesis objects are passed directly
* Everything else is wrapped in a Telekinesis object so it can be manipulated from the other side.  

You can quickly see this in action by creating a function that returns the argument's type:

```python
broker.entrypoint, _ = await tk.create_entrypoint(
  lambda x: str(type(x))
)
echo_type = await tk.Entrypoint()

print(await echo_type(1)) # >> "<class 'int'>"
print(await echo_type('hi')) # >> "<class 'str'>"
print(await echo_type(b'000')) # >> "<class 'bytes'>"
print(await echo_type(True)) # >> "<class 'bool'>"
print(await echo_type(None)) # >> "<class 'NoneType'>"

print(await echo_type([1,2,3])) # >> "<class 'list'>"
print(await echo_type(set([1,2,3])) # >> "<class 'set'>"
print(await echo_type({'a': 'A', 'b': 'B'})) # >> "<class 'dict'>"

print(await echo_type(lambda: None)) # >> "<class 'telekinesis.telekinesis.Telekinesis'>"
```

You can also see that the same logic applies to whatever a function returns:
```python
broker.entrypoint, _ = await tk.create_entrypoint(
  lambda: {'int': 10, 'str': 'asdf', 'list': [1, 2, lambda: None]}
)
return_dict = await tk.Entrypoint()

print(await return_dict()) 
# >> {'int': 10, 'str': 'asdf', 'list': [1, 2, ≈ function <lambda>...]}
```

One last thing to note is that whenever a Telekinesis object is passed back to its creator, it gets converted back to the original object it was wrapping. For example:

```python
broker.entrypoint, _ = await tk.create_entrypoint(
  lambda: lambda x: str(type(x))
)

outer_lambda = await tk.Entrypoint()

inner_lambda = await outer_lambda()

# Same as the previous echo_type example
print(await inner_lambda(123)) # "<class 'int'>
print(await inner_lambda(lambda: None)) # >> "<class 'telekinesis.telekinesis.Telekinesis'>"

# However, outer_lambda get's converted back to its original function 
print(await inner_lambda(outer_lambda)) # >> "<class 'function'>" 
# (instead of "<class telekinesis.telekinesis.Telekinesis'>")
```


## Security

Beware:
* Telekinesis assumes you know what you're doing: it won't stop you from sharing potentially dangerous functions or objects. For example, `numpy` objects have a `to_file` method that can modify files in your filesystem. Be mindful of what what you share and to whom.
* **Zero** security experts have gone through the code, checked for vulnerabilities and implemented fixes. Use at your discretion.

Having said that, Telekinesis boasts the following features aiming the library to be secure:

* All communication is End-to-End encrypted
* Every connection is authenticated with a private/public key pair
* Message brokers validate authorization token chains on every message
* Clients can only call functions and methods exposed to them
* Private attributes and methods (those whose name starts with '_') are unreachable by callers (except for `__call__`, `__init__`, `__add__`, and other standard dunder methods)

If you like hacking stuff (while being nice and good), you can help me find  vulnerabilities in this library, and submit them as issues [here](https://github.com/telekinesis-inc/telekinesis/issues).

## Other features

If you're like me and prefer reading code to written text, then: great news! You can read all the features built into telekinesis at [the python's tests folder](https://github.com/telekinesis-inc/telekinesis/tree/main/python/test) or at the [js spec file](https://github.com/telekinesis-inc/telekinesis/blob/main/js/src/telekinesis.spec.ts).

If you prefer written text and guides to code... I guess you'll have to wait for now, sorry :/

## Join me

So far I've been developing this by myself, mostly with the idea of making [serverless easy and powerful](https://www.telekinesis.cloud). However, if you like where this *"Just-in-time SDKs"* project is going (or think this is a cool idea like me), it'd be great to talk with you.

You can email me at [contact@telekinesis.cloud](contact@telekinesis.cloud) or chat with me on [Discord](https://discord.gg/p7Trmr2S)
