This README is intended as a basic overview, you can find more detailed documentation at [Telekinesis.Cloud](https://www.telekinesis.cloud).

# Telekinesis

Telekinesis is an **[experimental]** open source new way of defining online services. 

The idea is that you code like you would do locally (albeit an `await`), and then Telekinesis takes care of the rest:

* Communication is End-to-End encrypted
* No need to set up SSL, ports, networking, etc
* Only authorized agents can use your service
* Callers are securely limited in scope
* There are as few communication round trips as possible

### Installation:


```bash
pip install telekinesis # python
```


```bash
npm install telekinesis-js # javascript
```

### Basic Usage

Connect to a registry (here we'll use [Telekinesis.Cloud](https://www.telekinesis.cloud)) in two different places (could be just 2 Jupyter Notebooks): "Computer A" and "Computer B"

```python
# run this in both places

from telekinesis import authenticate

user = await authenticate('wss://telekinesis.cloud')
```

#### Manipulate remote objects:

###### Computer A: Publish an object

```python
x = np.arange(10) # Define an object

await user.set('x', x)  # Publish the object

# Note: Be careful who you share numpy objects with, the tofile() method that can affect your stored data
```

###### Computer B: Manipulate the object

```python
tk_x = await user.get('x') # Get the object that "A" published

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
await user.set(print, 'print')  # Publish the function
```
###### Computer B: Call the function
```python
tk_print = await user.get('print') # Get the object that "A" published

await tk_print('Hello A!')
```

> (Output, back at Computer A) `Hello A!`
