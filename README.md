# Telekinesis

*noun* the ability to move objects by means of ~~thought alone~~ internet communication, without physical means;

## Basic Idea

Telekinesis is built on the premise that developing, publishing and calling services across the internet shouldn't be fundamentally different to writing local code. 

Therefore, to create a new service, write a function or class and *publishe* it to a *hub* (more on this later):

```python
# Example: stateless service (function):
def hello(name='world'):
    # Document your services with docstrings, default values and good argument names
    return 'Hello ' + str(name)

# Publish and serve your new service in the telekinesis.cloud `hub` in one line:
service_hello = await node.publish('<your-username>/hello', hello)
```

To call this new service (on a separate computer), just *get* it from the *hub* and call it:
```python
hello = await node.get('<your-username>/hello')

# This should print 'Hello Awesome':
print(await hello('Awesome'))
```

Moreover, if you want to create a more complex service (i.e. with state and multiple methods), use classes:

```python
# define a stateful service (object):
class Counter:
    def __init__(self, initial_value=0):
        self.value = initial_value
    def increment(self, amount=1):
        self.value += amount
        return self.value

service_counter = await node.publish('<your-username>/Counter', Counter)
```

Then on the client side:
```python
Counter = await node.get('<your-username>/Counter')

# This instantiates the object
counter = await Counter(123)

# This should print 123:
print(counter.value)

# This should print 223:
print(await counter.increment(100))
```
## Main Features

- **Low latency**: Telekinesis is based on websocket communication, so SSL and authorization verification occurs at the connection initialization stage; service requests and responses send a single network package - no back and forth.
- **Role based authorization**: Telekinesis has a hierarchical, role-based, authorization system, which limits who can call and serve each service, making the services themselves simpler. **DO NOT TRUST YET!**
- **Limited surface area**: The client doesn't have to trust the code of the service provider and viceversa. The computation runs entirely on the service provider side, so the only the service signature and the arguments passed are shared. Moreover, the service can have any dependencies and secrets the developer needs.
- **No network set-up**: Once a hub is set-up and running, all the services created can start working, without having to set up ports, SSL or DNS.
- **Services feel local**: Telekinesis makes the service instances on the client side feel like local code, maintaining the argument names, default values and docstrings.

## Disclaimer

This is a **PROTOTYPE**, nobody has AUDITED this code yet, so:
- DO NOT TRUST with critical/confidential data.
- DO NOT TRUST with personal passwords.
- API will likely change.

## Getting Started

#### Installation:
```bash
pip install telekinesis
```

#### Connect to the telekinesis.cloud hub:
```python
from telekinesis import Node

# Connect to a Telekinesis `hub`
node = await Node('wss://telekinesis.cloud').connect()

# Telekinesis.cloud has `sign_up` and `sign_in` services to authenticate 
sign_in = await node.get('sign_in')

# This prompts you for username and password and checks it matches with the one you signed up with:
assert await sign_in() 

# Create a sample service: 
service_hello = await node.publish('<your-username>/hello', lambda name='World': 'Hello '+ str(name))

# Get this service (you would normally do this in a different computer/script):
hello = await node.get('<your-username>/hello')

# This should print 'Hello Awesome':
print(await hello('Awesome'))
```

#### Run a simple Telekinesis hub:
```python
from telekinesis import Hub

# Start a local Telekinesis `hub`
hub = await Hub().start()

# By default the hub runs in 'ws://localhost:3388' (same url the Node connects to by default)
```

**Note**: This simple hub doesn't have the authorization layer activated. You can check out `examples/telekinesis_cloud_website/server.py` and `examples/telekinesis_cloud_website/services/sign_in.py` to see how the role based authorization system is leveraged.

## API Reference

Telekinesis is still in early development, so the API is likely to change, and therefore its documentation will grow stale quickly.

The best reference (for now) to check the functionality are the tests at `python/test`. 

Each test is self contained and demonstrates one key functionality (i.e. having multiple workers for a service, delegating roles, etc.)

## Contribute

If you are interested in this concept, you can contribute by:
- Proposing API name changes (I'd rather make breaking changes now than later!)
- Finding bugs and security holes
- Submitting issues
- Adding tests (see `python/test` directory)
- Creating examples
- Helping with the docstrings

Any help is very appreciated!

You can also register your email at 'https://telekinesis.cloud" to be notified for updates (this also helps me gauge interest)
