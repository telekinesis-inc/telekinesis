This README is intended as a basic overview, you can find more detailed documentation at [Telekinesis.Cloud](https://telekinesis.cloud)'s website.

# Telekinesis

An open source library that **abstracts**, **optimizes** and **secures** RPC (Remote Procedure Calls), so that you can interact with objects and functions running on another computer, almost as if they were local.

##### Abstraction:

You don't think about communication at all, all you care about are remote objects and functions. However, given remote calls have latency, Telekinesis uses asynchronous communication. Just remember to `await` your remote calls.

##### Optimization:

Telekinesis optimizes communication in 3 ways:

- Message brokers are distributed: This minimizes the physical distance messages have to travel to go from caller to callee.
- Method chaining are bundled in a single message: Calling `obj.method_1().method_2().attribute` sends only one message over the network.
- Delegation: Remote objects and functions can be delegated so the new caller communicates directly with the object/function provider.

##### Security:

Since we want to avoid [RPC](https://en.wikipedia.org/wiki/Remote_procedure_call) to turn into [RCE](https://en.wikipedia.org/wiki/Arbitrary_code_execution), Telekinesis takes several steps to keep communication secure:

- All message payloads (everything but the route and authorization headers) are end-to-end encrypted. 
- All the messages are signed with session keys, and the edges completely control who who can communicate with a specific channel. 
- The truly paranoid can even hard-code session keys (used for message signing) and channel keys (used for encryption) to rule out any man-in-the-middle attacks.
- Telekinesis objects can have *masks* that block certain methods or attributed from being called remotely.
- With the exception of a few useful Dunder Methods (such as `__call__`, `__setitem__`, `__add__`...) private methods and attributes (whose names start with `_`) are masked by default.

### Installation:

Currently, only `python` is supported. The easiest way to install telekinesis is with `pip`:

```bash
pip install telekinesis
```

Telekinesis communicates through Websockets protocol, uses elliptic curve encryption and serializes data with BSON. Any language that supports these may be supported in the future.

The `js` directory contains a bundle file used to ensure `javascript` compatibility during development.

### Basic Usage

*The following examples assume there are 2 computers connected to a registry.*

#### Manipulate remote objects:

###### Computer A: Publish an object

```python
x = np.arange(10) # Define an object

await registry.set(x, 'x')  # Publish the object to a registry
```

###### Computer B: Manipulate the object

```python
tk_x = await registry.get('x') # Get the object that "A" published

print(tk_x)
```
> `(Output) ≈  array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]) `

```python
print(await tk_x.dtype) #  Get an attribute
```
> `(Output) ≈  dtype('int64')`

```python
print(await tk_x.max()) #  Call a method
```
> `(Output) ≈  array([9])`


#### Call remote functions:

###### Computer A: Publish a function

```python
await registry.set(print, 'print')  # Publish the function
```
###### Computer B: Call the function
```python
tk_print = await registry.get('print') # Get the object that "A" published

await tk_print('Hello A!')
```

> (Output, back at Computer A) `Hello A!`

