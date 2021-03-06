import { Channel, Header, RequestMetadata, Route, Session } from './client';

export class State {
  attributes: string[] | Map<string, any>;
  methods: Map<string, [string, string]>;
  pipeline: [string, Telekinesis | string | [any[], {}]][];
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

  toObject(mask?: Set<string>, cacheAttributes: boolean = false) {
    mask = mask || new Set<string>();
    return {
      attributes: this.attributes instanceof Map ?
        (cacheAttributes ?
          Array.from(this.attributes.keys()).filter(v => !(mask as Set<string>).has(v))
            .reduce((p: any, v: string) => { p[v] = (this.attributes as Map<string, any>).get(v); return p }, {}) :
          Array.from(this.attributes.keys()).filter(v => !(mask as Set<string>).has(v)) 
        ) :
        this.attributes.filter(v => !(mask as Set<string>).has(v)),
      methods: Array.from(this.methods.keys()).filter(v => !(mask as Set<string>).has(v))
        .reduce((p: any, v: string) => { p[v] = this.methods.get(v); return p }, {}),
      pipeline: this.pipeline.map(x => x),
      repr: this.repr,
      doc: this.doc,
      last_change: this.lastChange,
    }
  }
  clone() {
    return State.fromObject(this.toObject(undefined, true));
  }
  static fromObject(obj: any) {
    return new State(
      obj.attributes instanceof Array ? obj.attributes :
        Object.getOwnPropertyNames(obj.attributes || {}).reduce((p, v) => { p.set(v, obj.attributes[v]); return p }, new Map()),
      Object.getOwnPropertyNames(obj.methods || {}).reduce((p, v) => { p.set(v, obj.methods[v]); return p }, new Map()),
      obj.repr,
      obj.doc,
      obj.pipeline,
      obj.last_change,
    );
  }

  static fromTarget(target: Object) {
    let state = State.fromObject({
      attributes: Object.getOwnPropertyNames(target)
          .filter(x => x[0] !== '_')
          .reduce((p, v) => { (p as any)[v] = (target as any)[v]; return p }, {}),
      methods: Object.getOwnPropertyNames(Object.getPrototypeOf(target))
        .filter(x => !['constructor', 'arguments', 'caller', 'callee'].includes(x) && x[0] !== '_')
        .reduce((p, v) => { (p as any)[v] = ['(*args)', (target as any)[v].toString()]; return p }, {}),
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
export class Telekinesis extends Function {
  _target: Route | Object;
  _session: Session;
  _mask: Set<string>;
  _exposeTb: boolean;
  _maxDelegationDepth?: number;
  _compileSignatures: boolean;
  _parent?: Telekinesis;

  _state: State;

  _channel?: Channel;
  _clients?: Map<[string, string|null], any>;
  _onUpdateCallback?: any;
  _subscription?: Telekinesis;
  _subscribers: Set<Telekinesis>;

  _lastUpdate: number;
  _blockThen: boolean;
  _isTelekinesisObject: boolean;
  _proxy: any;

  constructor(
    target: Route | Object, session: Session, mask?: string[] | Set<string>, exposeTb: boolean = true,
    maxDelegationDepth?: number, compileSignatures: boolean = true, parent?: Telekinesis 
  ) {
    super();
    this._target = target;
    this._session = session;
    this._mask = (mask && ((mask instanceof Set) ? mask : mask.reduce((p, v) => { p.add(v); return p }, new Set<string>())))
      || new Set();
    this._exposeTb = exposeTb;
    this._maxDelegationDepth = maxDelegationDepth;
    this._compileSignatures = compileSignatures;
    this._parent = parent;

    this._subscribers = new Set();

    this._lastUpdate = Date.now();
    this._blockThen = false;
    this._isTelekinesisObject = true;

    this._proxy = new Proxy(this, {
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
        )
      },
      apply(target: Telekinesis, that: any, args: any[]) {
        return target._call(args);
      }
    });
    if (target instanceof Route) {
      this._state = new State();
      if (parent === undefined) {
        if (!session.routes.has([target.session, target.channel])) {
          session.routes.set([target.session, target.channel], {refcount: 0, delegations: new Set<[string, string|null]>()});
        }
        const o = this._session.routes.get([target.session, target.channel])
        if (o != undefined) {
          o.refcount += 1;
        }
      }
    } else {
      session.targets.set(target, (session.targets.get(target) || new Set()).add(this._proxy))
      this._state = State.fromTarget(target);
    }
    return this._proxy;
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
  _updateState(state: State) {
    this._state = state;
    this._onUpdateCallback && this._onUpdateCallback(this);
  }
  async _delegate(receiver: string | [string, string], parentChannel?: Channel) {
    let route: Route;
    let maxDelegationDepth = this._maxDelegationDepth;
    let extendRoute = true;
    let tokenHeaders: Header[] = [];

    if (this._target instanceof Route) {
      route = this._target.clone();
      maxDelegationDepth = undefined;
      if (receiver === '*') {
        throw "Cannot delegate remote Route to public '*'";
      }
    } else {
      if (this._channel === undefined || (this._channel?.isPublic && receiver === '*')) {
        if (this._channel) {
          this._channel.isPublic = true;
        } else {
          this._channel = await (new Channel(this._session, undefined, receiver === '*') as any) as Channel;
          this._channel.telekinesis = this;
        }
        this._channel.listen();
        tokenHeaders.push(this._channel.headerBuffer.pop() as Header)
      }
      if (this._channel.isPublic) {
        extendRoute = false;
      }
      route = await this._channel.route as Route;
    }

    if (extendRoute) {
      tokenHeaders.push(await this._session.extendRoute(route, receiver instanceof Array ? receiver[0] : receiver, maxDelegationDepth) as Header);
      if (this._session.routes.has([route.session, route.channel])) {
        this._session.routes.get([route.session, route.channel]).add(receiver instanceof Array ? receiver : [receiver, null]);
      }
    }
    route._parentChannel = parentChannel || this._channel;
    route._parentChannel?.headerBuffer.push(...tokenHeaders);


    return route;
  }
  _subscribe(callback?: any) {
    this._onUpdateCallback = callback;
    this._subscription = new Telekinesis(
      (newState: any) => {
        const state = State.fromObject(newState)
        state.pipeline = this._state.pipeline;
        this._state = state;
      }, this._session, undefined, this._exposeTb, this._maxDelegationDepth, this._compileSignatures
    )
    this._state.pipeline.push(['subscribe', this._subscription])
    return this
  }
  async _handleRequest(channel: Channel, metadata: RequestMetadata, payload: {}) {
    let pipeline;
    let replyTo;

    try {
      if (this._clients && !this._clients.has(metadata.caller.session)) {
        this._clients.set(metadata.caller.session, {lastState: null, cacheAttributes: null });
        this._clients.delete([metadata.caller.session[0], null]);
      }
      if ((payload as any)['close'] !== undefined) {
        this._clients?.delete(metadata.caller.session);
        for (const delegation of ((payload as any)['close'] as Array<[string, string|null]>)) {
          if (this._clients && !this._clients.has(delegation)) {
            this._clients.set(delegation, {lastState: null, cacheAttributes: null});
            if (delegation[1] !== null) {
              this._clients.delete([delegation[0], null]);
            }
          }
        }
        if ((this._clients?.size == 0) && (this._channel?.isPublic === false)) {
          await this._close();
        }

      } else if ((payload as any)['ping'] !== undefined) {
        await channel.send(metadata.caller, { repr: this._state.repr, timestamp: this._state.lastChange })
      } else if ((payload as any)['pipeline'] !== undefined) {
        if ((payload as any)['reply_to']) {
          replyTo = Route.fromObject((payload as any)['reply_to'])
          await replyTo.validateTokenChain(await this._session.sessionKey.publicSerial());
        }
        pipeline = this._decode((payload as any)['pipeline']) as [];
        // console.log(`${metadata.caller.session.slice(0, 4)} called ${pipeline.length}`)

        let ret = await this._execute(metadata, pipeline, true);

        if (ret instanceof Telekinesis && ret._target instanceof Route && (
          ret._target.session !== await this._session.sessionKey.publicSerial() ||
          !this._session.channels.has(ret._target.channel)
        )) {
          await (ret as Telekinesis)._forward(ret._state.pipeline, replyTo || metadata.caller);
        } else {
          if (replyTo !== undefined) {
            const newChannel = new Channel(this._session)
            try {
              await newChannel.send(replyTo, {
                return: await this._encode(ret, replyTo.session, newChannel),
                repr: this._state.repr,
                timestamp: this._state.lastChange,
              })
            } finally {
              await newChannel.close();
            }
          } else {
            await channel.send(metadata.caller, {
              return: await this._encode(ret, metadata.caller.session),
              repr: this._state.repr,
              timestamp: this._state.lastChange,
            })
          }
        }
      }
    } catch (e) {
      console.error(`Telekinesis request error with payload ${JSON.stringify(payload, undefined, 2)}, ${e}`)
      this._state.pipeline = [];
      try {
        const errMessage = { error: (this._exposeTb ? e : e.name) };
        if (replyTo !== undefined) {
          const newChannel = new Channel(this._session)
          try {
            await newChannel.send(replyTo, errMessage);
          } finally {
            await newChannel.close();
          }
        } else {
          await channel.send(metadata.caller, errMessage);
        }
      } finally { }
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
  async _execute(metadata?: RequestMetadata, pipeline?: [string, Telekinesis | string | [string[], {}]][], breakOnTelekinesis: boolean = false) {
    if (this._target instanceof Promise) {
      const oldTarget = this._target;
      this._target = await this._target;
      const set = (this._session.targets.get(oldTarget) as Set<Telekinesis>);
      set.delete(this._proxy)
      if (!set.size) {
        this._session.targets.delete(oldTarget)
      }
      if (this._target instanceof Route) {
        if (this._parent === undefined) {
          if (!this._session.routes.has([this._target.session, this._target.channel])) {
            this._session.routes.set([this._target.session, this._target.channel], {refcount: 0, delegations: new Set<[string, string|null]>()});
          }
          const o = this._session.routes.get([this._target.session, this._target.channel])
          if (o != undefined) {
            o.refcount += 1;
          }
        }
      } else {
        this._session.targets.set(this._target, (this._session.targets.get(this._target) || new Set()).add(this._proxy))
      }
    }
    pipeline = pipeline || [];

    pipeline = this._state.pipeline.concat(pipeline);

    this._state.pipeline = [];

    if (this._target instanceof Route) {
      return await this._forward(pipeline);
    }
    function exc(x: any) {
      if (x._blockThen !== undefined && x._lastUpdate && x._state && x._state.pipeline.length) {
        return new Promise(r => x._execute(metadata).then(r));
      }
      return x;
    }
    let target: any = this._target;
    let prevTarget = target;
    let touched: Set<Telekinesis> = this._session.targets.get(this._target) || new Set();

    for (let step in pipeline) {
      if (breakOnTelekinesis && target instanceof Telekinesis && target._target instanceof Route && (
        target._target.session !== await this._session.sessionKey.publicSerial() ||
        !this._session.channels.has(target._target.channel)
      )) {
        const oldState = target._state;
        target = new Telekinesis(target._target, target._session, target._mask, target._exposeTb, target._maxDelegationDepth, target._compileSignatures, target._parent);
        target._state = oldState.clone();
        target._state.pipeline.push(...pipeline.slice(parseInt(step)));
        target._blockThen = true;
        break;
      }
      touched = new Set([...touched, ...(this._session.targets.get(target) || [])])
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

        let ar = (pipeline[step][1] as [string[], {}])[0] as [];
        let args: any[] = [];
        for (let i in ar) {
          args[i] = exc(ar[i]);
          if (args[i] instanceof Promise) {
            args[i] = await args[i];
          }
        }

        if (target._tk_inject_first === true) {
          args = [metadata as RequestMetadata, ...args];
        }

        try {
          target = target.call(prevTarget, ...args);
        } catch (e) {
          try {
            target = new target(...args);
          } catch (e2) {
            throw (e)
          }
        }
        if (target instanceof Promise) {
          target = await target;
        }
        if (!breakOnTelekinesis && target instanceof Telekinesis && target._target instanceof Route && target._state.pipeline.length) {
          target = await target._execute();
        }
      } else if (action === 'subscribe') {
        const cb = pipeline[step][1] as Telekinesis;
        const r = cb._target as Route;
        if (metadata && r.validateTokenChain(metadata.caller.session[0])) {
          const tk = Telekinesis._reuse(
            target, this._session, this._mask, this._exposeTb, this._maxDelegationDepth, this._compileSignatures
          )
          if (tk._clients && !tk._clients.has(r.session)) {
            tk._clients.set(r.session, {lastState: null, cacheAttributes: null });
            tk._clients.delete([r.session[0], null]);
          }
          const o = tk._clients?.get(r.session);
          o.cacheAttributes = true;
          tk._subscribers.add(cb);

        }
      }
    }

    for (const tk of touched) {
      tk._state = State.fromTarget(tk._target);

      const subscribers = Array.from(tk._subscribers)
      if (subscribers) {
        const state = State.fromTarget(tk._target).toObject(tk._mask, true)
        subscribers.map((s: Telekinesis) => s(state)._execute())
      }
    }

    return target;

  }
  _timeout(seconds: number) {
    return new Promise((res: any, rej: any) => { setTimeout(() => rej('Timeout'), seconds * 1000); this._execute().then(res) })
  }
  async _sendRequest(channel: Channel, request: {}) {
    let response = {};
    await channel.send(this._target as Route, request);

    if ((request as any).reply_to === undefined) {
      let tup = await channel.recv();
      response = (tup as any)[1];

      if (Object.getOwnPropertyNames(response).includes('return')) {
        // console.log((response as any)['return'])
        let out = this._decode((response as any)['return'], (this._target as Route).session[0])
        if (out?._isTelekinesisObject === true) {
          out._lastUpdate = Date.now();
          out._blockThen = true;
        }
        // console.log(out)
        return out
      } else if (Object.getOwnPropertyNames(response).includes('error')) {
        throw (response as any).error;
      }
    }
  }
  async _close() {
    try {
      if (this._target instanceof Route) {
        const o = this._session.routes.get([this._target.session, this._target.channel]);
        if (o.refcount !== undefined) {
          o.refcount -= 0;
          if (o.refcount <= 0) {
            const newChannel = new Channel(this._session);
            await newChannel.send(this._target, {close: Array.from(o.delegations) || []})
          }
        }
      } else {
        this._session.targets.get(this._target)?.delete(this);
        if (this._session.targets.get(this._target)?.size === 0) {
          this._session.targets.delete(this._target);
        }
        await this._channel?.close();
      }
    } catch(e) {
      console.error(e)
    }
  }
  async _forward(pipeline: [string, Telekinesis | string | [string[], {}]][], replyTo?: Route) {
    let newChannel = new Channel(this._session);
    try {
      if (replyTo !== undefined) {
        const tokenHeader = await this._session.extendRoute(replyTo, (this._target as Route).session[0])
        newChannel.headerBuffer.push(tokenHeader as Header)
      }
      return await this._sendRequest(
        newChannel,
        {
          reply_to: replyTo?.toObject(),
          pipeline: await this._encode(pipeline, (this._target as Route).session, newChannel)
        }
      )
    } finally {
      await newChannel.close()
    }
  }
  async _encode(target: any, receiver: [string, string], channel?: Channel, traversalStack?: Map<any, [string, [string, any]]>, blockRecursion: boolean = false) {

    let id = 0;

    if (traversalStack === undefined) {
      traversalStack = new Map();
    } else {
      if (traversalStack.has(target)) {
        return (traversalStack.get(target) as [string, [string, any]])[0].toString();
      }
      id = traversalStack.size;
    }
    let out = [id, ['placeholder', null as any]]
    traversalStack.set(target, out as [string, [string, any]])

    if (['number', 'boolean', 'string'].includes(typeof target) || target === null || target === undefined) {
      out[1] = [({
        number: Number.isInteger(target) ? 'int' : 'float',
        string: 'str',
        boolean: 'bool',
        object: 'NoneType',
        undefined: 'NoneType',
      } as any)[typeof target] as string, target]
    } else if (typeof target !== 'undefined' && target instanceof Uint8Array) {
      out[1] = ['bytes', target];
    } else if (typeof target !== 'undefined' && (target instanceof Array || target instanceof Set)) {
      let children: string[] = [];
      let arr = target instanceof Array ? target : Array.from(target.values());
      for (let v in arr) {
        children[v] = await this._encode(arr[v], receiver, channel, traversalStack, blockRecursion)
      }
      out[1] = [target instanceof Array ? 'list' : 'set', children];
    } else if (typeof target !== 'undefined' && Object.getPrototypeOf(target).constructor.name === 'Object') {
      let children = {};
      for (let v in target) {
        (children as any)[v] = await this._encode(target[v], receiver, channel, traversalStack, blockRecursion);
      }
      out[1] = ['dict', children];
    } else if (typeof target !== 'undefined' && target instanceof Route) {
      out[1] = ['route', target.toObject()];
    } else {
      let obj: Telekinesis;
      if (target._isTelekinesisObject === true) {
        obj = target;
      } else {
        obj = Telekinesis._reuse(target, this._session, this._mask, this._exposeTb, this._maxDelegationDepth,
          this._compileSignatures, undefined)
      }
      if (!(target._target instanceof Route)) {
        if (obj._clients?.has(receiver) === false) {
          obj._clients?.set(receiver, {lastState: null, cacheAttributes: null});
        }
        if (receiver[1] !== null) {
          obj._clients?.delete([receiver[0], null]);
        }
      }

      let route = await obj._delegate(receiver[0], channel || this._channel) as Route;
      out[1] = ['obj', [
        route.toObject(),
        await this._encode(obj._state.toObject(this._mask, !blockRecursion && obj._clients?.get(receiver)?.cacheAttributes), receiver, channel, traversalStack, true)
      ]]

    }

    traversalStack.delete(target);
    traversalStack.set(target, out as [string, [string, any]])

    if (id === 0) {
      let output = Array.from(traversalStack.values()).reduce((p: any, v: any) => { p[v[0]] = v[1]; return p }, {});
      // console.log('encoded', target, output)
      return output
    }
    return id.toString();
  }
  _decode(inputStack: {}, callerId?: string, root?: string, outputStack: Map<string, any> = new Map()) {
    let out: any;
    if (root === undefined) {
      // console.log(inputStack)
      root = "0";
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
          out = arr.reduce((p, v) => { p.add(v); return p }, new Set());
        } else {
          out = arr;
        }
        outputStack.set(root, out);

      } else if (['range', 'slice'].includes(typ)) {
        let n = Math.ceil((obj[1] - obj[0]) / obj[2])
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
      } else if (typ === 'route') {
        out = Route.fromObject(obj);
      } else {
        // console.log(typ, obj)
        let route = Route.fromObject(obj[0]);
        let state = State.fromObject(this._decode(inputStack, callerId, obj[1], outputStack));

        if (this._parent ) {
          this._target = route;
          this._updateState(state);
          this._parent = undefined;
          if (!this._session.routes.has([route.session, route.channel])) {
            this._session.routes.set([route.session, route.channel], {refcount: 0, delegations: new Set<[string, string|null]>()})
          }
          const o = this._session.routes.get([route.session, route.channel])
          if (o != undefined) {
            o.refcount += 1;
          }
          out = this._proxy;
        } else {
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
      }

      outputStack.set(root, out);
      return out;
    }
  }
  static _fromState(state: State, target: Route | Object, session: Session, mask?: string[] | Set<string>, exposeTb: boolean = true,
    maxDelegationDepth?: number, compileSignatures: boolean = true, parent?: Telekinesis) {
    let t = new Telekinesis(target, session, mask, exposeTb, maxDelegationDepth, compileSignatures, parent);
    t._state = state;
    return t
  }
  static _reuse(
    target: Route | Object, session: Session, mask?: string[] | Set<string>, exposeTb: boolean = true,
    maxDelegationDepth?: number, compileSignatures: boolean = true, parent?: Telekinesis
  ) {
    const kwargs = { target, session, mask, exposeTb, maxDelegationDepth, compileSignatures }
    return Array.from(session.targets.get(target) || [])
      .reduce((p, c: any) => p || (Object.entries(kwargs)
        .reduce((pp, cc: [string, any]) => pp && c['_' + cc[0]] === cc[1], true) && c), undefined) ||
      new Telekinesis(target, session, mask, exposeTb, maxDelegationDepth, compileSignatures, parent)
  }
}
export function injectFirstArg(func: any) {
  func._tk_inject_first = true;
  return func;
}