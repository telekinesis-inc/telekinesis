import { serialize, deserialize } from "bson";
import { deflate, inflate } from 'zlib'
import { bytesToInt, intToBytes  } from "./helpers"
import { PublicKey, PrivateKey, SharedKey, Token } from "./cryptography"
import { Telekinesis } from "./telekinesis";

const webcrypto = typeof crypto.subtle !== 'undefined' ? crypto : require('crypto').webcrypto;

export type Header = ["send", any] | ["token", any] | ["listen", any] | ["close", any]

export class Connection {
  // RESEND_TIMEOUT: number;
  // MAX_SEND_RETRIES: number;

  session: Session;
  url: string;
  websocket?: WebSocket;
  brokerId?: string;
  tOffset: number;
  entrypoint?: Route;

  constructor(session: Session, url: string = 'ws://localhost:8776') {
    // this.RESEND_TIMEOUT = 2; (not implemented yet) // sec
    // this.MAX_SEND_RETRIES = 3;

    this.session = session;
    this.url = url;
    this.tOffset = 0;

    session.connections.push(this);
  }
  async connect() {
    if (this.websocket !== undefined) {
      this.websocket.close();
      this.websocket = undefined;
    }
    this.websocket = (typeof WebSocket !== 'undefined' ? 
      new WebSocket(this.url) :
      new (require('ws'))(this.url)) as WebSocket;
    // this.websocket.onerror = (ev) => {
    //   console.error(ev)
    //   new Promise(async r => {
    //     await new Promise(r => setTimeout(r, 1000));
    //     await this.connect(); r()}).then(() => console.log('Reconnected'))
    // }
    // this.websocket.onclose = this.websocket.onerror;
    let queue: Uint8Array[] = [];
    let waiting: ((data: Uint8Array) => void)[] = [];

    let recv = () => new Promise((r: (data: Uint8Array) => void) => 
      queue.length > 0 ? r(queue.splice(0,1)[0]) : waiting.push(r)
    );

    this.websocket.onmessage = async (m: MessageEvent) => {
      let a = typeof URL.createObjectURL === 'undefined' ? 
        m.data :
        await fetch(URL.createObjectURL(m.data)).then(r => r.arrayBuffer());

      let data = new Uint8Array(a);
      
      if (waiting.length > 0) {
        for (var i in waiting) {
          let res = waiting.pop();
          res && res(data);
        }
      } else {
        queue.push(data);
      }
    }

    let challenge = await recv();
    let signature = await this.session.sessionKey.sign(challenge) as Uint8Array;

    let pk = new TextEncoder().encode(await this.session.sessionKey.publicSerial());

    let sentChallenge = webcrypto.getRandomValues(new Uint8Array(32));
    let sentMetadata = new TextEncoder().encode(
      JSON.stringify({
        version: '0.1.1'
      })
    )

    this.websocket.send(new Uint8Array([
      ...signature,
      ...pk,
      ...sentChallenge,
      ...sentMetadata
    ]));
    
    this.tOffset = Date.now()/1000 - bytesToInt(challenge.slice(32,36))
    let m: Uint8Array = await recv()

    let brokerId = new TextDecoder().decode(m.slice(64,152))
    let publicKey = new PublicKey('verify', brokerId)
    
    let metadata = JSON.parse(new TextDecoder().decode(m.slice(152))) as {};

    // console.log(metadata)
    if (metadata.hasOwnProperty('entrypoint') && (metadata as any).entrypoint) {
      this.entrypoint = Route.fromObject((metadata as any).entrypoint as any);
    }

    try {
      await publicKey.verify(m.slice(0, 64), sentChallenge)
      this.websocket.onmessage = async m => this.recv(m);
      this.brokerId = brokerId;
      // console.log('connected to ' + this.brokerId)
    } catch {
      this.websocket.close()
      delete this.websocket
    }
  }
  async recv(messageObj: MessageEvent) {
    
    let a = typeof URL.createObjectURL === 'undefined' ? 
      messageObj.data :
      await fetch(URL.createObjectURL(messageObj.data)).then(r => r.arrayBuffer());
    
    let message = new Uint8Array(a);
    let signature = message.slice(0, 64);
    let timestamp = bytesToInt(message.slice(64, 68));

    if (this.session.checkNoRepeat(signature, timestamp + this.tOffset)) {
      let hLen = bytesToInt(message.slice(68,70));
      let pLen = bytesToInt(message.slice(70,73));
      
      let headers = JSON.parse(new TextDecoder().decode(message.slice(73, 73+hLen))) as Header[];
      let fullPayload = message.slice(73+hLen, 73+hLen+pLen);

      for (let i in headers) {
        if (headers[i][0] === 'send') {
          // console.log(headers)
          let body = headers[i][1];
          let publicKey = new PublicKey('verify', body.source.session);

          try {
            await publicKey.verify(signature, message.slice(64, 73+hLen+65+32))
            if (this.session.channels.has(body.destination.channel)) {
              if (fullPayload[0] == 255) {console.log('ACK');return} // Ignore ACKs
              let mid = fullPayload[0] == 0 ? signature : fullPayload.slice(1, 65)
              await this.send(
                [['send', {source: body.destination, destination: body.source}]],
                undefined,
                undefined,
                new Uint8Array([ 255, ...mid ])
              ); // Send ACK 
              let payload = fullPayload.slice(65 + 32)
              let hash = new Uint8Array(await webcrypto.subtle.digest('SHA-256', payload))
              if (fullPayload.slice(65, 65+32).reduce((p, v, i) => p && v === hash[i], true)) {
                if (mid.reduce((p, v, i) => p && v === signature[i], true) 
                || this.session.checkNoRepeat(mid, timestamp + this.tOffset)) {
                  let channel = this.session.channels.get(body.destination.channel)
                  channel && await channel.handleMessage(
                    Route.fromObject(body.source),
                    Route.fromObject(body.destination),
                    payload
                  )
                }
              }
            }
          } catch(e) {console.error('On receive message error: ' + e)}
        }
      }
    }


  }
  async send(headers: Header[], payload: Uint8Array = new Uint8Array(), bundleId?: Uint8Array, reply?: Uint8Array) {
    if (this.websocket === undefined || this.websocket.readyState > 1) {
      await this.connect()
    }
    if (this.websocket !== undefined) {
      let h = new TextEncoder().encode(JSON.stringify(headers))
      let r = reply ? reply : new Uint8Array(65)
      let p = new Uint8Array(await webcrypto.subtle.digest('SHA-256', payload))
      let m = new Uint8Array([
        ...intToBytes(Date.now()/1000 - this.tOffset, 4),
        ...intToBytes(h.length, 2),
        ...intToBytes(payload.length+32+65, 3),
        ...h,
        ...r,
        ...p
      ])
    
      let s = await this.session.sessionKey.sign(m) as Uint8Array;
      this.websocket.send(new Uint8Array([...s, ...m, ...payload]));
    }
  }
  clear(bundleId: Uint8Array) {}
}

export class Session {
  sessionKey: PrivateKey;
  channels: Map<string, Channel>;
  connections: Connection[];
  seenMessages: [Set<Uint8Array>, Set<Uint8Array>, number];
  issuedTokens: Map<string, [Token, Token?]>;

  constructor() {
    this.sessionKey = new PrivateKey('sign');
    this.channels = new Map();
    this.connections = [];
    this.seenMessages = [new Set(), new Set(), 0];
    this.issuedTokens = new Map();
  }
  checkNoRepeat(signature: Uint8Array, timestamp: number) {
    let now = Date.now() / 1000;
    let lead = Math.floor(now / 60);
    
    if (this.seenMessages[2] != lead) {
      this.seenMessages[lead % 2] = new Set();
      this.seenMessages[2] = lead;
    }

    if (((now - 60 + 4) <= timestamp) && (timestamp < (now + 4))) {
      if (!this.seenMessages[0].has(signature) && !this.seenMessages[1].has(signature)) {
        (this.seenMessages[lead % 2] as Set<Uint8Array>).add(signature)
        return true
      }
    }
    return false
  }
  async issueToken(target: Token | string, receiver: string, maxDepth?: number) {
    let tokenType: 'root'|'extension';
    let prevToken;
    let asset;
    let token;

    if (target instanceof Token) {
      tokenType = 'extension';
      prevToken = target;
      asset = target.signature;
    } else {
      tokenType = 'root';
      asset = target;
    }
    let cached = false;
    for (var tokens of this.issuedTokens.values()) {
      token = tokens[0];
      if (
        token.asset === asset &&
        token.receiver === receiver &&
        token.tokenType === tokenType &&
        token.maxDepth === maxDepth
      ) {
        for (var j in this.connections) {
          if (this.connections[j].brokerId && token.brokers.includes(this.connections[j].brokerId as string)) {
            prevToken = tokens[1];
            cached = true;
          }
        }
      }
    }
    if (!cached) {
      token = new Token(
        await this.sessionKey.publicSerial() as string,
        this.connections.map(c => c.brokerId as string),
        receiver,
        asset as string,
        tokenType || 'root',
        maxDepth
      );
      let signature = await token.sign(this.sessionKey) as string;
      this.issuedTokens.set(signature, [token, prevToken])
    }

    return ["token", ["issue", token && token.encode(), prevToken && prevToken.encode()]]
  }
  async extendRoute(route: Route, receiver: string, maxDepth?: number) {
    let tokenHeader;
    if (route.session === await this.sessionKey.publicSerial()) {
      tokenHeader = await this.issueToken(route.channel, receiver, maxDepth)
    } else {
      for (var i in route.tokens) {
        let token = await Token.decode(route.tokens[i]) as Token;
        if (token.receiver === await this.sessionKey.publicSerial()) {
          route.tokens = route.tokens.slice(0, parseInt(i)+1);
        }
      }
      let token = await Token.decode(route.tokens[-1]) as Token;
      tokenHeader = await this.issueToken(token, receiver, maxDepth);
    }
    route.tokens = [tokenHeader[1][1] as string];
    return tokenHeader;
  }
  clear(bundleId: Uint8Array) {
    for (var i in this.connections) {
      let connection = this.connections[i]
      connection.clear(bundleId)
    }
  }
  async send(headers: Header[], payload: Uint8Array = new Uint8Array([]), bundleId?: Uint8Array) {
    for (var i in this.connections) {
      let connection = this.connections[i]
      await connection.send(headers, payload, bundleId)
    }
  }
}

export class Channel {
  MAX_PAYLOAD_LEN: number;
  MAX_COMPRESSION_LEN: number;
  MAX_OUTBOX: number;

  session: Session;
  channelKey: PrivateKey;
  isPublic: boolean;
  route?: Route;

  headerBuffer: Header[];
  chunks: Map<number, Map<number, Uint8Array>>;
  messages: [Route, {}][];
  waiting: (([]) => void)[];
  initLocks: ((channel: Channel) => void)[];
  
  telekinesis?: Telekinesis;

  then: ((resolve: (ret: any) => void) => void) | undefined;

  constructor(session: Session, isPublic=false) {
    this.MAX_PAYLOAD_LEN = 2**19;
    this.MAX_COMPRESSION_LEN = 2**19;
    this.MAX_OUTBOX = 2**4;

    this.session = session;
    this.channelKey = new PrivateKey('derive');
    this.isPublic = isPublic;

    this.headerBuffer = [];
    this.chunks = new Map();

    this.messages = [];
    this.waiting = [];
    this.initLocks = [];
    
    this.then = this._then;

    this.channelKey.publicSerial().then(channelId => {
      this.session.channels.set(channelId as string, this)
      this.session.sessionKey.publicSerial().then(sessionId => {
        this.route = new Route(
          this.session.connections.map(c => c.brokerId) as string[],
          sessionId as string,
          channelId as string,
          []);
        for (var i in this.initLocks) {
          this.initLocks[i](this);
        }
        this.initLocks = [];
      })
    })
  }
  async handleMessage(source: Route, destination: Route, rawPayload: Uint8Array) {
    if (await this.validateTokenChain(source.session, destination.tokens)) {
      let sharedKey = new SharedKey( this.channelKey, source.channel)
      let rawChunk = new Uint8Array(
        await sharedKey.decrypt(rawPayload.slice(16,), rawPayload.slice(0, 16)) as Uint8Array
      )

      let payloadSer = new Uint8Array();
      if (bytesToInt(rawChunk.slice(0,4)) === 0) {
        payloadSer = rawChunk.slice(4,);
      } else {
        let i = bytesToInt(rawChunk.slice(0, 2));
        let n = bytesToInt(rawChunk.slice(2, 4));
        let mid = bytesToInt(rawChunk.slice(4, 8));
        let chunk = rawChunk.slice(8);

        if (!this.chunks.has(mid)) {
          this.chunks.set(mid, new Map())
        }
        let chunksMap = (this.chunks.get(mid) as Map<number, Uint8Array>)
        chunksMap.set(i, chunk);

        if (chunksMap.size === n) {
          for (let ii=0; ii<n; ii++) {
            payloadSer = new Uint8Array([...payloadSer, ...(chunksMap.get(ii) as Uint8Array)])
          }
          this.chunks.delete(mid);
        } else {
          return
        }
      }

      let payload;
      if (payloadSer[0] === 0) {
        payload = deserialize(payloadSer.slice(1));
      } else {
        payload = deserialize(await new Promise((r, rej) => {
          inflate(payloadSer.slice(1), (err, buff) => err ? rej(err) : r(new Uint8Array(buff)))
        }) as Uint8Array);
      }
      if (this.waiting.length > 0) {
        let resolve = this.waiting.pop(); 
        resolve && resolve([source, payload]);
      } else {
        this.messages.push([source, payload]);
      }
    } else {
      console.error(
        `Invalid Tokens: ${source.session.slice(0, 4)} ${source.channel.slice(0, 4)} ||| ` +
        `${destination.session.slice(0, 4)} ${destination.channel.slice(0, 4)} ` + 
        `[${destination.tokens}]`
      );
    }
  }
  async recv() {
    return new Promise(resolve => 
      this.messages.length > 0 ?
        resolve(this.messages.splice(0, 1)[0]) :
        this.waiting.push(resolve))

  }
  listen() {
    function pushListenHeader(channel: Channel) {
      if (channel.route !== undefined) {
        let obj = {
          brokers: channel.route.brokers,
          session: channel.route.session,
          channel: channel.route.channel,
          is_public: channel.isPublic,
        };
        channel.headerBuffer.push(['listen', obj])
      }
    }
    if (this.route === undefined) {
      this.initLocks.push(pushListenHeader);
    } else {
      pushListenHeader(this);
    }
    this.then = this._then;
    return this;
  }
  async send(destination: Route, payloadObj: object) {
    if (this.route === undefined) {
      await new Promise(r => this.initLocks.push(r))
    }
    if (this.route !== undefined) {
      async function* encryptSlice(
        payload: Uint8Array, 
        maxPayload: number, 
        sharedKey: SharedKey, 
        mid: Uint8Array, 
        n: number, 
        i:number): any
      {
        let chunk = new Uint8Array();
        if (i < n) {
          if (n === 1) {
            chunk = new Uint8Array([...[0, 0, 0, 0], ...payload]);
          } else {
            if (n > 2 ** 16) {
              throw `Payload size ${payload.length / 2**20} MiB is too large`;
            }
            chunk = new Uint8Array([
              ...intToBytes(i, 2),
              ...intToBytes(n, 2),
              ...mid,
              ...payload.slice(i * maxPayload, (i+1) *  maxPayload)
            ]);
          }
          let nonce = webcrypto.getRandomValues(new Uint8Array(16));
          yield new Uint8Array([...nonce, ...(await sharedKey.encrypt(chunk, nonce) as Uint8Array)]);
          yield * await encryptSlice(payload, maxPayload, sharedKey, mid, n, i+1);
        }
      }
      async function execute(channel: Channel, header: Header, sliceGenerator: any, mid: Uint8Array) {
        for await (let slice of sliceGenerator) {
          await channel.execute(header, slice, mid)
        }
      }
      let sourceRoute = this.route.clone();
      this.headerBuffer.push(await this.session.extendRoute(sourceRoute, destination.session) as Header);
      this.listen();

      let payload = new Uint8Array(serialize(payloadObj));

      if (payload.length < this.MAX_COMPRESSION_LEN) {
        payload = new Uint8Array([255, ...(await new Promise((r, rej) => {
          deflate(payload, (err, buf) => err ? rej(err) : r(new Uint8Array(buf)))
        }) as Uint8Array)]);

      } else {
        payload = new Uint8Array([0, ...payload]);
      }

      let mid = webcrypto.getRandomValues(new Uint8Array(4));

      let sharedKey = new SharedKey(
        this.channelKey, 
        destination.channel)

      let header = [
        'send', {
          source: sourceRoute.toObject(),
          destination: destination.toObject()
        }
      ] as Header

      let n = Math.floor((payload.length - 1) / this.MAX_PAYLOAD_LEN) + 1;
      let nTasks = Math.min(n, this.MAX_OUTBOX);

      let gen = encryptSlice(payload, this.MAX_PAYLOAD_LEN, sharedKey, mid, n, 0);
      
      try {
        await Promise.all(Array(nTasks).fill(0).map(() => new Promise(r => { execute(this, header, gen, mid).then(r) })))
      } catch {
        this.session.clear(mid);
      }
      return new Promise(r=> r(this));
    }
  }
  async execute(header?: Header, payload: Uint8Array = new Uint8Array(), bundleId?: Uint8Array) {
    if (this.route === undefined) {
      await new Promise(r => this.initLocks.push(r))
    }
    if (this.route !== undefined) {
      if (this.headerBuffer.length > 0 || header !== undefined) {
        await this.session.send(
          header !== undefined ? this.headerBuffer.concat([header]): this.headerBuffer,
          payload,
          bundleId)
        this.headerBuffer = [];
      }
    }
    this.then = undefined;
    return new Promise(r=> r(this));
  }
  _then(resolve: ((ret: any) => void)) {
    if (this.headerBuffer.length === 0 && this.route !== undefined) { 
      this.then = undefined;
      resolve(this);
    } else {
      this.execute().then(() => resolve(this))
    }
  }
  async close() {
    for (var i in this.session.connections) {
      let connection = this.session.connections[i]
      let obj = await this.route?.toObject()
      await connection.send([['close', obj]])
    }
    for (var i in this.waiting) {
      this.waiting[i]([undefined, undefined])
    }
  }
  async validateTokenChain(sourceId: string, tokens: string[]) {
    let sessionId = await this.session.sessionKey.publicSerial()
    if (this.isPublic || (sourceId === sessionId)) { return true }
    if (tokens.length === 0) { return false }

    let asset = await this.channelKey.publicSerial();
    let lastReceiver = sessionId;
    let maxDepth = null;

    for (let depth in tokens) {
      let token;
      try {
        token = await Token.decode(tokens[depth]) as Token;
      } catch(e) {
        if (e === 'Invalid Signature') { return false }
        throw e;
      }
      if ((token.asset === asset) && (token.issuer === lastReceiver)) {
        if (token.issuer === sessionId) {
          if (!this.session.issuedTokens.has(token.signature as string)) { return false }
        }
        if (token.maxDepth) {
          if (maxDepth === null || ((token.maxDepth + depth) < maxDepth)) {
            maxDepth = token.maxDepth + depth;
          }
        }
        if (maxDepth === null || (depth < maxDepth)) {
          lastReceiver = token.receiver;
          asset = token.signature;
          if (lastReceiver === sourceId) { return true }
          continue;
        }
      }
      return false;
    }
    return false;
  }
}

export class Route {
  brokers: string[];
  session: string;
  channel: string;
  tokens: string[];

  constructor(brokers: string[], session: string, channel: string, tokens: string[]) { // 
    this.brokers = brokers;
    this.session = session;
    this.channel = channel;
    this.tokens = tokens;
  }
  toObject() {
    return {
      brokers: this.brokers,
      session: this.session,
      channel: this.channel,
      tokens: this.tokens,
    }
  }
  clone() {
    return Route.fromObject(this.toObject());
  }
  static fromObject(obj: {brokers: string[], session: string, channel: string, tokens: string[]}) {
    return new Route(obj.brokers, obj.session, obj.channel, obj.tokens)
  }
}