import { Session, Channel, Route, Header } from './client'; 
import { bytesToInt } from './helpers';
  
const webcrypto = typeof crypto.subtle !== 'undefined' ? crypto : require('crypto').webcrypto;

export class State {
  attributes: string[] | Map<string, any>;
  methods: Map<string,[string, string]>;
  pipeline: [string, string | [any[], {}]][];
  repr: string;
  doc?: string;
  lastChange?: number;

  constructor(
    attributes?: string[] | Map<string, any>, methods?: Map<string, [string, string]>, repr?: string, doc?: string,
    pipeline?: [string, string | [any[], {}]][], lastChange?: number
  ) {
    this.attributes = attributes || [];
    this.methods = methods || new Map();
    this.pipeline = pipeline || [];
    this.repr = repr || '';
    this.doc = doc;
    this.lastChange = lastChange;
  }

  toObject(mask?: Set<string>) {
    mask = mask || new Set<string>();
    return {
      attributes: this.attributes instanceof Map ?
        Array.from(this.attributes.keys()).filter(v => !(mask as Set<string>).has(v))
          .reduce((p: any, v: string) => {p[v] = (this.attributes as Map<string, any>).get(v); return p}, {}) :
        this.attributes.filter(v => !(mask as Set<string>).has(v)),
      methods: Array.from(this.methods.keys()).filter(v => !(mask as Set<string>).has(v))
        .reduce((p: any, v: string) => {p[v] = this.methods.get(v); return p}, {}),
      pipeline: this.pipeline.map(x=>x),
      repr: this.repr,
      doc: this.doc,
      last_change: this.lastChange,
    }
  }
  clone() {
    return State.fromObject(this.toObject());
  }
  static fromObject(obj: any) {
    return new State(
      obj.attributes instanceof Array ? obj.attributes : 
        Object.getOwnPropertyNames(obj.attributes || {}).reduce((p, v) => {p.set(v, obj.attributes[v]); return p}, new Map()),
      Object.getOwnPropertyNames(obj.methods || {}).reduce((p, v) => {p.set(v, obj.methods[v]); return p}, new Map()),
      obj.repr,
      obj.doc,
      obj.pipeline,
      obj.last_change,
    );
  }
  
  static fromTarget(target: Object, cacheAttributes: boolean) {
    let state = State.fromObject({
      attributes: cacheAttributes ?
        Object.getOwnPropertyNames(target)
          .filter(x => x[0] !== '_')
          .reduce((p, v) => {(p as any)[v] = (target as any)[v]; return p}, {}) :
        Object.getOwnPropertyNames(target).filter(x => x[0] !== '_'),
      methods: Object.getOwnPropertyNames(Object.getPrototypeOf(target))
        .filter(x => !['constructor', 'arguments', 'caller', 'callee'].includes(x) && x[0] !== '_')
        .reduce((p, v) => {(p as any)[v] = ['(*args)', (target as any)[v].toString()]; return p}, {}),
      repr: (target.toString && target.toString()) || '',
      doc: (target as any).__doc__,
      pipeline: [],
      last_change: Date.now() / 1000,
    })
    if (target instanceof Function) {
      state.methods.set('__call__', ['(*args)', target.toString()]);
    }
    return state;
  }
}
export class Listener {
  channel: Channel;
  listenTask?: Promise<void>;
  currentTasks: Map<string, Promise<void>>;

  constructor(channel: Channel) {
    this.channel = channel;
    this.currentTasks = new Map();
  }
  setCallback(callback: (listener: Listener, reply: Route, payload: {}) => Promise<void>) {
    this.listenTask = new Promise(async r => await this.listen());
    return this;
  }
  async listen() {
    console.log('Started Listening')
    while (true) {
      try {
        await (this.channel.listen() as any);
        while (true) {
          let message = await this.channel.recv() as [Route?, {}?];
          // console.log('message', message)
          if (message[0] === undefined) {
            return;
          }
          let id = Date.now().toString();

          this.currentTasks.set(
            id,
            new Promise(async r => {
              await ((this.channel.telekinesis as Telekinesis)._handleRequest)(this, message[0] as Route, message[1] as {})
            }).then(() => {
              this.currentTasks.delete(id);
            })
          )
        }
      } catch(e) {
        console.error(e);
      }
    }
  }
  async close(closePublic: boolean = false) {
    if (closePublic || !this.channel.isPublic) {
      await this.channel.close();
    }
  }
}
export class Telekinesis extends Function {
  _target: Route | Object;
  _session: Session;
  _mask: Set<string>;
  _exposeTb: boolean;
  _maxDelegationDepth?: number;
  _compileSignatures: boolean;
  _parent?: Telekinesis;
  _cacheAttributes: boolean;

  _listeners: Map<Route, Listener>;
  _state: State;

  _lastUpdate: number;
  _blockThen: boolean;
  _isTelekinesisObject: boolean;

  constructor(
    target: Route | Object, session: Session, mask?: string[] | Set<string>, exposeTb: boolean = true, 
    maxDelegationDepth?: number, compileSignatures: boolean = true, parent?: Telekinesis, cacheAttributes: boolean = false
  ) {
    super();
    this._target = target;
    this._session = session;
    this._mask = (mask && ((mask instanceof Set)? mask : mask.reduce((p, v) => {p.add(v); return p}, new Set<string>())))
      || new Set();
    this._exposeTb = exposeTb;
    this._maxDelegationDepth = maxDelegationDepth;
    this._compileSignatures = compileSignatures;
    this._parent = parent;
    this._cacheAttributes = cacheAttributes;

    this._listeners = new Map();
    if (target instanceof Route) {
      this._state = new State();
    } else {
      this._state = State.fromTarget(target, cacheAttributes);
    }
    this._lastUpdate = Date.now();
    this._blockThen = false;
    this._isTelekinesisObject = true;

    return new Proxy(this, {
      get(target: Telekinesis, prop: string) {
        if (prop[0] === '_') {
          return (target as any)[prop];
        }
        if (prop === 'then') {
          if (target._blockThen && (Date.now() - target._lastUpdate) < 300) {
            return new Promise(r => r(target));
          }
          return (async (r: any) => r(await target._execute()))
        }

        let state = target._state.clone()
        state.pipeline.push(['get', prop])

        return Telekinesis._fromState(
          state, 
          target._target,
          target._session,
          target._mask,
          target._exposeTb,
          target._maxDelegationDepth,
          target._compileSignatures,
          target,
          target._cacheAttributes)
      },
      apply(target: Telekinesis, that: any, args: any[]) {
        return target._call(args);
      }
    });
  }
  _getRootState(): State {
    if (this._parent !== undefined) {
      return this._parent._getRootState();
    }
    return this._state;
  }
  _last() {
    if ((this._state.pipeline.length === 1) && (this._state.pipeline[0][0] === 'get') && (this._state.attributes instanceof Map)) {
      return (this._state.attributes as Map<string, any>).get(this._state.pipeline[0][1] as string);
    }
    return undefined;
  }
  async _addListener(channel: Channel) {
    let route = (await (channel as any)).route;
    channel.telekinesis = this;

    this._listeners.set(route, new Listener(channel).setCallback(this._handleRequest));
    return route;
  }
  async _delegate(receiverId: string, parentChannel?: Channel) {
    let route: Route;
    let maxDelegationDepth = this._maxDelegationDepth;
    if (this._target instanceof Route) {
      route = this._target.clone();
      maxDelegationDepth = undefined;
    } else {
      route = await this._addListener(new Channel(this._session));
      let listener = this._listeners.get(route) as Listener;

      if (parentChannel === undefined) {
        parentChannel = listener.channel;
      }
    }

    let tokenHeader = await this._session.extendRoute(route, receiverId, maxDelegationDepth);
    (parentChannel as Channel).headerBuffer.push(tokenHeader as Header);

    return route;
  }
  async _handleRequest(listener: Listener, reply: Route, payload: {}) {
    // console.log('handleRequest!!', this, listener, reply);
    try {
      if ((payload as any)['close'] !== undefined) {
        await listener.close();
      } else if ((payload as any)['ping'] !== undefined) {
        await listener.channel.send(reply, {repr: this._state.repr, timestamp: this._state.lastChange})
      } else if ((payload as any)['pipeline'] !== undefined) {
        let pipeline = this._decode((payload as any)['pipeline']) as [];
        // console.log(`${reply.session.slice(0, 4)} called ${pipeline.length}`)

        let ret = await this._execute(listener, reply, pipeline);

        await listener.channel.send(reply, {
          return: await this._encode(ret, reply.session, listener),
          repr: this._state.repr,
          timestamp: this._state.lastChange,
        })
      }
    } catch(e) {
      console.error(`Telekinesis request error with payload ${payload}, ${e}`)
      this._state.pipeline = [];
      try {
        await listener.channel.send(reply, {error: (this._exposeTb? e : e.name )})
      } finally {}
    }
  }
  _call(this: Telekinesis, args: any[], kwargs?: any) {
    let state = this._state.clone()
    state.pipeline.push(['call', [args, kwargs || {}]])

    return Telekinesis._fromState(
      state, 
      this._target,
      this._session,
      this._mask,
      this._exposeTb,
      this._maxDelegationDepth,
      this._compileSignatures,
      this)
  }
  async _execute(listener?: Listener, reply?: Route, pipeline?: [string, string | [string[], {}]][]) {
    pipeline = pipeline || [];

    pipeline = this._state.pipeline.concat(pipeline);

    this._state.pipeline = [];

    if (this._target instanceof Route) {
      let newChannel = new Channel(this._session);
      try {
        return await this._sendRequest(
          newChannel,
          {pipeline: await this._encode(pipeline, this._target.session, new Listener(newChannel))}
        )
      } finally {
        await newChannel.close()
      }
    }
    async function exc(x: any) {
      if (x._blockThen !== undefined && x._lastUpdate && x._state && x._state.pipeline) {
        return await x._execute(listener, reply);
      }
      return x;
    }
    let target: any = this._target;
    let prevTarget = target;

    for (let step in pipeline) {
      let action = pipeline[step][0];
      if (action === 'get') {
        let arg = pipeline[step][1] as string;
        // console.log(`${action} ${arg} ${target}`);
        if (arg[0] === '_' || this._mask.has(arg)) {
          throw 'Unauthorized!';
        }
        prevTarget = target;
        target = (target as any)[arg];
        if (target === undefined) {
          throw TypeError(`Attribute ${arg} not found`)
        }
        if (target instanceof Function) {
          target.bind(prevTarget)
        }
      } else if (action === 'call') {
        // console.log(`${action} ${target}`);
        
        let ar = pipeline[step][1][0] as [];
        let args: any[] = [];
        for (let i in ar) {
          args[i] = await exc(ar[i]);
        }
        // console.log(args)

        if (target._tk_inject_first === true) {
          args = [reply as Route, ...args];
        }
        
        const isConstructor = (x: any) => {
          try {
            return !!(new (new Proxy(x, {construct() {return isConstructor}}))())
          } catch(e) {
            return false
          }
        }
        if (isConstructor(target)) {
          target = await new target(...args);
        } else {
          target = await target.call(prevTarget, ...args);
        }
      }
    }

    this._state = State.fromTarget(this._target, this._cacheAttributes);
    this._state.lastChange = Date.now() / 1000;

    return target;

  }
  async _sendRequest(channel: Channel, request: {}) {
    let response = {};
    await channel.send(this._target as Route, request);

    let tup = await channel.recv();
    response = (tup as any)[1];


    // console.log(tup)
    if (Object.getOwnPropertyNames(response).includes('return')) {
      // console.log((response as any)['return'])
      let out = this._decode((response as any)['return'], (this._target as Route).session)
      if (out && out._isTelekinesisObject === true) {
        out._lastUpdate = Date.now();
        out._blockThen = true;
      }
      // console.log(out)
      return out
    }
  }
  async _encode(target: any, receiverId: string, listener?: Listener, traversalStack?: Map<any, [string, [string, any]]>, blockRecursion: boolean = false) {

    let id = bytesToInt(webcrypto.getRandomValues(new Uint8Array(4))).toString()
    let outputStack = false;

    if (traversalStack === undefined) {
      traversalStack = new Map();
      outputStack = true;
    } else {
      if (traversalStack.has(target)) {
        return (traversalStack.get(target) as [string, [string, any]])[0]
      }
    }
    let out = [id, ['placeholder', null as any]]
    traversalStack.set(target, out as [string, [string, any]])

    if (['number', 'boolean', 'string'].includes(typeof target) || target === null || target === undefined) {
      out[1] = [({
        number: Number.isInteger(target)? 'int': 'float',
        string: 'str',
        boolean: 'bool',
        object: 'NoneType',
        undefined: 'NoneType',
      } as any)[typeof target] as string, target]
    } else if (typeof target !== 'undefined' && target instanceof Uint8Array) {
      out[1] = ['bytes', target];
    } else if (typeof target !== 'undefined' && (target instanceof Array || target instanceof Set)) {
      let children: string[] = [];
      let arr = target instanceof Array? target: Array.from(target.values());
      for (let v in arr) {
        children[v] = await this._encode(arr[v], receiverId, listener, traversalStack, blockRecursion)
      }
      out[1] = [target instanceof Array? 'list': 'set', children];
    } else if (typeof target !== 'undefined' && Object.getPrototypeOf(target).constructor.name === 'Object') {
      let children = {};
      for (let v in target) {
        (children as any)[v] = await this._encode(target[v], receiverId, listener, traversalStack, blockRecursion);
      }
      out[1] = ['dict', children];
    } else {
      let obj: Telekinesis;
      if (target._isTelekinesisObject === true) {
        obj = target;
      } else {
        obj = new Telekinesis(target, this._session, this._mask, this._exposeTb, this._maxDelegationDepth, 
          this._compileSignatures, undefined, this._cacheAttributes && !blockRecursion)
      }

      let route = await obj._delegate(receiverId, (listener as Listener).channel) as Route;
      out[1] = ['obj', [
        route.toObject(), 
        await this._encode(obj._state.toObject(this._mask), receiverId, listener, traversalStack, true)
      ]]

    }

    traversalStack.delete(target);
    traversalStack.set(target, out as [string, [string, any]])

    // console.log('prelim', id, target, out[1])

    if (outputStack) {
      let output = Array.from(traversalStack.values()).reduce((p: any, v:any) => {p[v[0]] = v[1]; return p}, {});
      output['root'] = id;
      // console.log('encoded', target, output)
      return output
    }
    return id
  }
  _decode(inputStack: {}, callerId?: string, root?: string, outputStack: Map<string, any> = new Map()) {
    let out: any;
    if (root === undefined) {
      root = (inputStack as any)["root"];
    }
    if (root !== undefined) {
      if (outputStack.has(root)) {
        return outputStack.get(root)
      }

      let typ = (inputStack as any)[root][0] as string;
      let obj = (inputStack as any)[root][1] as any;

      if (['int', 'float', 'str', 'bool', 'NoneType'].includes(typ)) {
        // console.log(obj)
        out = obj;
      } else if (typ === 'bytes') {
        out = obj.buffer;
      } else if (['list', 'tuple', 'set'].includes(typ)) {
        let arr = Array(obj.length);
        outputStack.set(root, arr);
        for (let k in (obj as [])) {
          arr[k] = this._decode(inputStack, callerId, obj[k], outputStack);
        }
        if (typ === 'set') {
          out = arr.reduce((p, v) => {p.add(v); return p}, new Set());
        } else {
          out = arr;
        }
        outputStack.set(root, out);

      } else if (['range', 'slice'].includes(typ)) {
        // TODO: Handle slice more gracefully! (maybe TK.Slice object?)
        let n = Math.ceil( (obj[1] - obj[0]) / obj[2] )
        if (n <= 0) {
          out = [];
        } else {
          out = new Array(n).fill(0).map((_, i) => obj[0] + obj[2] * i);
        } 
      } else if (typ === 'dict') {
        out = {}
        outputStack.set(root, out);
        
        for (let i in Object.getOwnPropertyNames(obj)) {
          let k = Object.getOwnPropertyNames(obj)[i];
          out[k] = this._decode(inputStack, callerId, obj[k], outputStack);
        }
        outputStack.set(root, out);
      } else {
        // console.log(typ, obj)
        let route = Route.fromObject(obj[0]);
        let state = State.fromObject(this._decode(inputStack, callerId, obj[1], outputStack));

        out = Telekinesis._fromState(
          state,
          route,
          this._session,
          this._mask,
          this._exposeTb,
          this._maxDelegationDepth,
          this._compileSignatures,
        )
      }

      outputStack.set(root, out);
      return out;
    }
  }
  static _fromState(state: State, target: Route | Object, session: Session, mask?: string[] | Set<string>, exposeTb: boolean = true, 
    maxDelegationDepth?: number, compileSignatures: boolean = true, parent?: Telekinesis, cacheAttributes: boolean = false) {
    let t = new Telekinesis(target, session, mask, exposeTb, maxDelegationDepth, compileSignatures, parent, cacheAttributes);
    t._state = state;
    return t
  }
}