import { deserialize, serialize } from "bson";
import { deflate, inflate } from "pako";
import { PrivateKey, PublicKey, SharedKey, Token } from "./cryptography";
import { bytesToInt, intToBytes, b64encode, b64decode } from "./utils";
import { createWebSocket } from "./fakeWebSocket";

const isNode = typeof global !== 'undefined' && (global as any)?.isNode == true;
const version = '0.1.88';
const webcrypto = isNode ? global.crypto : crypto;

export type Header = ["send", any] | ["listen", any] | ["close", any]

export class Connection {
  RESEND_TIMEOUT: number;
  MAX_SEND_RETRIES: number;

  session: Session;
  url: string;
  websocket?: WebSocket;
  brokerId?: string;
  brokerPeers?: string[];
  tOffset: number;
  entrypoint?: Route;
  awaitingAck: Map<string, any>;

  constructor(session: Session, url: string = 'ws://localhost:8776') {
    this.RESEND_TIMEOUT = 2; // sec
    this.MAX_SEND_RETRIES = 3;

    this.session = session;
    this.url = url;
    this.tOffset = 0;
    this.awaitingAck = new Map();

    session.connections.push(this);
  }
  connect(retryCount: number = 0): Promise<Connection | undefined> {
    return new Promise(async (resolve, reject) => {
      if (this.websocket !== undefined) {
        this.websocket.close();
        this.websocket = undefined;
      }
      this.websocket = createWebSocket(this.url) as WebSocket;
      this.websocket.onerror = e => {
        if (retryCount < this.MAX_SEND_RETRIES) {
          console.warn(`Failed connecting to ${this.url}. Retrying: ${retryCount + 1} of ${this.MAX_SEND_RETRIES}`)
          setTimeout(async () => resolve(await this.connect(retryCount + 1)), this.RESEND_TIMEOUT * 1000);
        } else {
          reject(e);
        }
      }
      let queue: Uint8Array[] = [];
      let waiting: ((data: Uint8Array) => void)[] = [];

      let recv = () => new Promise((r: (data: Uint8Array) => void) =>
        queue.length > 0 ? r(queue.splice(0, 1)[0]) : waiting.push(r)
      );

      this.websocket.onmessage = async (m: MessageEvent) => {
        let a = isNode?
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

      let pk = new TextEncoder().encode(await this.session.sessionKey.publicSerial(false));

      let sentChallenge = webcrypto.getRandomValues(new Uint8Array(32));
      let sentMetadata = new TextEncoder().encode(
        JSON.stringify({
          version: version
        })
      )

      this.websocket.send(new Uint8Array([
        ...signature,
        ...pk,
        ...sentChallenge,
        ...sentMetadata
      ]));

      this.tOffset = Date.now() / 1000 - bytesToInt(challenge.slice(32, 36))
      let m: Uint8Array = await recv()

      let brokerId = new TextDecoder().decode(m.slice(64, 152))
      let publicKey = new PublicKey('verify', brokerId)

      let metadata = JSON.parse(new TextDecoder().decode(m.slice(152))) as {};

      // console.log(metadata)
      if (metadata.hasOwnProperty('entrypoint') && (metadata as any).entrypoint) {
        this.entrypoint = Route.fromObject((metadata as any).entrypoint as any);
      }
      if (metadata.hasOwnProperty('peers') && (metadata as any).peers) {
        this.brokerPeers = (metadata as any).peers;
      }

      try {
        await publicKey.verify(m.slice(0, 64), sentChallenge)
      } catch (e) {
        this.websocket.close()
        delete this.websocket
        reject(e);
        return;
      }
      this.websocket.onmessage = async m => this.recv(m);
      this.brokerId = brokerId;
      // this.websocket.onerror = console.warn;
      this.websocket.onerror = e => {
        console.warn(`Connection to ${this.url} closed: ${JSON.stringify(e, undefined, 2)}. Reconnecting...`)
        setTimeout(() => this.connect(), this.RESEND_TIMEOUT * 1000);
      }
      resolve(this);
    })
  }
  async recv(messageObj: MessageEvent) {

    let a = isNode ?
      messageObj.data :
      await fetch(URL.createObjectURL(messageObj.data)).then(r => r.arrayBuffer());

    let message = new Uint8Array(a);
    let signature = message.slice(0, 64);
    let timestamp = bytesToInt(message.slice(64, 68));

    if (this.session.checkNoRepeat(signature, timestamp + this.tOffset)) {
      let hLen = bytesToInt(message.slice(68, 70));
      let pLen = bytesToInt(message.slice(70, 73));

      let headers = JSON.parse(new TextDecoder().decode(message.slice(73, 73 + hLen))) as Header[];
      let fullPayload = message.slice(73 + hLen, 73 + hLen + pLen);

      for (let i in headers) {
        if (headers[i][0] === 'send') {
          // console.log(headers)
          let body = headers[i][1];
          let publicKey = new PublicKey('verify', body.source.session[0]);

          try {
            await publicKey.verify(signature, message.slice(64, 73 + hLen + 65 + 32))
            if (this.session.channels.has(body.destination.channel)) {
              if (fullPayload[0] == 255) {
                // console.log('ACK');
                this.ack(body.source.session[0], b64encode(fullPayload.slice(1, 65)));

              } else {
                let mid = fullPayload[0] == 0 ? signature : fullPayload.slice(1, 65)
                await this.send(
                  [['send', { source: body.destination, destination: body.source }]],
                  undefined,
                  undefined,
                  new Uint8Array([255, ...mid])
                ); // Send ACK 
                let payload = fullPayload.slice(65 + 32)
                let hash = new Uint8Array(await webcrypto.subtle.digest('SHA-256', payload))
                if (fullPayload.slice(65, 65 + 32).reduce((p, v, i) => p && v === hash[i], true)) {
                  if (mid.reduce((p, v, i) => p && v === signature[i], true)
                    || this.session.checkNoRepeat(mid, timestamp + this.tOffset)) {
                    let channel = this.session.channels.get(body.destination.channel)
                    channel && await channel.handleMessage(
                      Route.fromObject(body.source),
                      Route.fromObject(body.destination),
                      payload,
                      message.slice(0, 73 + hLen + 65 + 32)
                    )
                  }
                }
              }
            }
          } catch (e) { console.error('On receive message error: ' + e) }
        }
      }
    }
  }
  send(headers: Header[], payload: Uint8Array = new Uint8Array(), bundleId?: Uint8Array, reply?: Uint8Array) {
    return new Promise(async (res, rej) => {
      const encode = async (messageId?: string, retry?: number) => {
        let h = new TextEncoder().encode(JSON.stringify(headers))
        let r = reply ? reply : (
          retry && messageId ?
            new Uint8Array([retry, ...b64decode(messageId)]) :
            new Uint8Array(65)
        )
        let p = new Uint8Array(await webcrypto.subtle.digest('SHA-256', payload))
        let m = new Uint8Array([
          ...intToBytes(Date.now() / 1000 - this.tOffset, 4),
          ...intToBytes(h.length, 2),
          ...intToBytes(payload.length + 32 + 65, 3),
          ...h,
          ...r,
          ...p
        ])

        let s = await this.session.sessionKey.sign(m) as Uint8Array;
        return [s, new Uint8Array([...s, ...m, ...payload])]
      }

      // Ensure connection is ready
      if (this.websocket === undefined || this.websocket.readyState > 1) {
        try {
          await this.connect();
        } catch (e) {
          rej(new Error(`Connection failed: ${e}`));
          return;
        }
      }

      let [s, mm] = await encode();
      const expectAck = headers.map(([a, _]) => a).includes('send') && reply == undefined;
      const messageId = b64encode(s);

      // Try to send the message
      try {
        if (this.websocket !== undefined) {
          this.websocket.send(mm);
        } else {
          throw new Error('WebSocket not connected');
        }
      } catch (e) {
        // On send failure, reset websocket and reject
        console.warn(`WebSocket send failed: ${e}`);
        this.websocket = undefined;
        rej(new Error(`Send failed: ${e}`));
        return;
      }

      if (!expectAck) {
        res(undefined);
        return;
      }

      // Wait for ACK with timeout
      const ackTimeout = setTimeout(() => {
        if (this.awaitingAck.has(messageId)) {
          this.awaitingAck.delete(messageId);
          this.websocket = undefined; // Reset connection on timeout
          rej(new Error('ACK timeout'));
        }
      }, this.RESEND_TIMEOUT * 1000);

      // Store headers, resolve function, and timeout so we can clear it on ACK
      this.awaitingAck.set(messageId, [headers, res, ackTimeout]);

      // Note: ACK will be received via this.ack() which calls the resolve function
      // stored in awaitingAck
    })
  }
  ack(sourceId: string, messageId: string) {
    if (this.awaitingAck.has(messageId)) {
      const [headers, resolve, timeout] = this.awaitingAck.get(messageId);
      // console.log(sourceId, messageId, headers)
      if (headers !== undefined) {
        for (let [action, content] of headers) {
          if (action === 'send' && content.destination.session[0] === sourceId) {
            this.awaitingAck.delete(messageId);
            if (timeout) clearTimeout(timeout);
            resolve(undefined);
          }
        }
      }
    }
  }
  clear(bundleId: Uint8Array) { }
  close() {
    if (this.websocket) {
      this.websocket.onerror = () => undefined;
      this.websocket.close(1000);
    }
  }
}

export class Session {
  sessionKey: PrivateKey;
  instanceId: string;
  channels: Map<string, Channel>;
  connections: Connection[];
  seenMessages: [Set<string>, Set<string>, number];
  issuedTokens: Map<string, [Token, Token?]>;
  targets: Map<any, Set<any>>;
  routes: Map<string, any>;
  messageListener?: (requestMetadata: RequestMetadata) => null
  scoreWeights: {
    transport_ws: number;
    transport_http: number;
    short_success: number;
    long_success: number;
    low_load: number;
    broker_affinity: number;
    connected: number;
  };

  constructor(sessionKey?: string) {
    this.sessionKey = new PrivateKey('sign', sessionKey);
    this.instanceId = b64encode(Uint8Array.from(webcrypto.getRandomValues(new Uint8Array(6))));
    this.channels = new Map();
    this.connections = [];
    this.seenMessages = [new Set(), new Set(), 0];
    this.issuedTokens = new Map();
    this.targets = new Map();
    this.routes = new Map();
    // Scoring weights for connection priority decisions
    this.scoreWeights = {
      transport_ws: 2.0,
      transport_http: 0.0,
      short_success: 5.0,
      long_success: 2.0,
      low_load: 3.0,
      broker_affinity: 4.0,
      connected: 100.0,  // Heavily prioritize already-connected connections
    };
  }
  toString() {
    return `Session ${this.sessionKey.repr?.slice(0, 4)} ${this.instanceId.slice(0, 2)}`
  }
  _connectionScore(connection: Connection, route?: Route): number {
    let score = 0.0;

    // Connection readiness - heavily prioritize already-connected connections
    if (connection.websocket && connection.websocket.readyState === 1) {
      score += this.scoreWeights.connected;
    }

    // Transport type
    if ((connection as any).type === 'ws') {
      score += this.scoreWeights.transport_ws;
    } else {
      score += this.scoreWeights.transport_http;
    }

    // Success rates
    const shortRate = (connection as any).success_10s ?? 1.0;
    const longRate = (connection as any).success_1m ?? 1.0;
    score += shortRate * this.scoreWeights.short_success;
    score += longRate * this.scoreWeights.long_success;

    // Load (lower awaiting ack count is better)
    const load = connection.awaitingAck?.size ?? 0;
    score += Math.max(0, this.scoreWeights.low_load - load);

    // Broker affinity
    if (route && route.brokers && connection.brokerId) {
      try {
        const idx = route.brokers.indexOf(connection.brokerId);
        if (idx !== -1) {
          score += this.scoreWeights.broker_affinity / (idx + 1);
        }
      } catch (e) {
        // ignore
      }
    }

    return score;
  }
  checkNoRepeat(signature: Uint8Array, timestamp: number) {
    let now = Date.now() / 1000;
    let lead = Math.floor(now / 60);
    let encodedSignature = b64encode(signature);

    if (this.seenMessages[2] != lead) {
      this.seenMessages[lead % 2] = new Set();
      this.seenMessages[2] = lead;
    }

    if (((now - 60 + 4) <= timestamp) && (timestamp < (now + 4))) {
      if (!this.seenMessages[0].has(encodedSignature) && !this.seenMessages[1].has(encodedSignature)) {
        (this.seenMessages[lead % 2] as Set<string>).add(encodedSignature)
        return true
      }
    }
    return false
  }
  async issueToken(target: Token | string, receiver: string, maxDepth?: number) {
    let tokenType: 'root' | 'extension';
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
            break;
          }
        }
        if (cached) {
          break;
        }
      }
    }
    if (!cached) {
      token = new Token(
        await this.sessionKey.publicSerial(false) as string,
        this.connections.map(c => c.brokerId as string),
        receiver,
        asset as string,
        tokenType || 'root',
        maxDepth
      );
      let signature = await token.sign(this.sessionKey) as string;
      this.issuedTokens.set(signature, [token, prevToken])
    }

    return token && token.encode()
  }
  async extendRoute(route: Route, receiver: string, maxDepth?: number) {
    let newTokenStr;
    if (route.session[0] === await this.sessionKey.publicSerial(false)) {
      newTokenStr = await this.issueToken(route.channel, receiver, maxDepth) as string;
      route.tokens = [newTokenStr];
      return;
    } else {
      for (var i in route.tokens) {
        let token = await Token.decode(route.tokens[i]) as Token;
        if (token.receiver === await this.sessionKey.publicSerial(false)) {
          route.tokens = route.tokens.slice(0, parseInt(i) + 1);
        }
        if (token.receiver === receiver) {
          route.tokens = route.tokens.slice(0, parseInt(i) + 1);
          return;
        }
      }
      let token = await Token.decode(route.tokens[route.tokens.length - 1]) as Token;
      newTokenStr = await this.issueToken(token, receiver, maxDepth);
    }
    route.tokens.push(newTokenStr as string);
  }
  clear(bundleId: Uint8Array) {
    for (var i in this.connections) {
      let connection = this.connections[i]
      connection.clear(bundleId)
    }
  }
  async send(headers: Header[], payload: Uint8Array = new Uint8Array([]), bundleId?: Uint8Array, route?: Route) {
    const exceptions: Error[] = [];

    // Sort connections once by priority
    const sortedConnections = [...this.connections].sort(
      (a, b) => this._connectionScore(b, route) - this._connectionScore(a, route)
    );

    if (sortedConnections.length === 0) {
      throw new Error('Session not connected');
    }

    // Prepare a round-robin over all connections and their retry budgets
    const sendPlan: [Connection, number][] = [];
    const maxRetries = Math.max(...sortedConnections.map(c => c.MAX_SEND_RETRIES));
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      for (const conn of sortedConnections) {
        if (attempt < conn.MAX_SEND_RETRIES) {
          sendPlan.push([conn, attempt]);
        }
      }
    }

    for (let i = 0; i < sendPlan.length; i++) {
      const [conn, attempt] = sendPlan[i];
      try {
        // Add timeout wrapper - allow time for reconnection + ACK wait
        // Connection retry can take up to MAX_SEND_RETRIES * RESEND_TIMEOUT
        // Plus ACK timeout of RESEND_TIMEOUT
        await Promise.race([
          conn.send(headers, payload, bundleId),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error('Send timeout')), conn.RESEND_TIMEOUT * 5 * 1000)
          )
        ]);
        return; // success
      } catch (e) {
        exceptions.push(e as Error);
      }
    }

    // exhausted all retries
    throw new Error(`All send attempts failed: ${exceptions.map(e => e.message).join(', ')}`);
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
  chunks: Map<string, Map<number, [Uint8Array, RequestMetadata]>>;
  messages: [RequestMetadata, {}][];
  waiting: (([]) => void)[];
  initLocks: ((channel: Channel) => void)[];

  telekinesis?: any;

  then: ((resolve: (ret: any) => void) => void) | undefined;

  constructor(session: Session, channelKey?: string, isPublic = false) {
    this.MAX_PAYLOAD_LEN = 2 ** 19;
    this.MAX_COMPRESSION_LEN = 2 ** 19;
    this.MAX_OUTBOX = 2 ** 4;

    this.session = session;
    this.channelKey = new PrivateKey('derive', channelKey);
    this.isPublic = isPublic;

    this.headerBuffer = [];
    this.chunks = new Map();

    this.messages = [];
    this.waiting = [];
    this.initLocks = [];

    this.then = this._then;

    this.channelKey.publicSerial(false).then(channelId => {
      this.session.channels.set(channelId as string, this)
      this.session.sessionKey.publicSerial(false).then(sessionId => {
        this.route = new Route(
          this.session.connections.map(c => c.brokerId) as string[],
          [sessionId as string, this.session.instanceId],
          channelId as string,
          []);
        for (var i in this.initLocks) {
          this.initLocks[i](this);
        }
        this.initLocks = [];
      })
    })
  }
  toString() {
    return `Channel ${this.session.sessionKey.repr?.slice(0, 4)} ${this.session.instanceId.slice(0, 2)} - ${this.channelKey.repr?.slice(0, 4)}`
  }
  async handleMessage(source: Route, destination: Route, rawPayload: Uint8Array, proof: Uint8Array) {
    if (await this.validateTokenChain(source.session[0], destination.tokens)) {
      let sharedKey = new SharedKey(this.channelKey, source.channel)
      let rawChunk = new Uint8Array(
        await sharedKey.decrypt(rawPayload.slice(16,), rawPayload.slice(0, 16)) as Uint8Array
      )
      let metadata = new RequestMetadata(
        this.session,
        source,
        [{ raw_payload: rawPayload, shared_key: sharedKey.key, proof: proof }]);

      let payloadSer = new Uint8Array();
      if (bytesToInt(rawChunk.slice(0, 4)) === 0) {
        payloadSer = rawChunk.slice(4,);
      } else {
        let i = bytesToInt(rawChunk.slice(0, 2));
        let n = bytesToInt(rawChunk.slice(2, 4));
        let mid = source.session[0] + bytesToInt(rawChunk.slice(4, 8));
        let chunk = rawChunk.slice(8);

        if (!this.chunks.has(mid)) {
          this.chunks.set(mid, new Map())
        }
        let chunksMap = (this.chunks.get(mid) as Map<number, [Uint8Array, RequestMetadata]>)
        chunksMap.set(i, [chunk, metadata]);

        if (chunksMap.size === n) {
          for (let ii = 0; ii < n; ii++) {
            let ch = (chunksMap.get(ii) as [Uint8Array, RequestMetadata]);
            payloadSer = new Uint8Array([...payloadSer, ...ch[0]])
            if (ii != i) {
              metadata.rawMessages?.push(ch[1]);
            }
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
        const ff = inflate(payloadSer.slice(1));
        payload = deserialize(ff);
      }

      if (this.session.messageListener) {
        this.session.messageListener(metadata)
      }
      // console.log(`<<< ${source} ${destination} ${Object.keys(payload)}`)
      if (this.telekinesis && this.telekinesis._handleRequest) {
        this.telekinesis._handleRequest(this, metadata, payload)
      } else if (this.waiting.length > 0) {
        let resolve = this.waiting.pop();
        resolve && resolve([metadata, payload]);
      } else {
        this.messages.push([metadata, payload]);
      }
    } else {
      console.error(
        `Invalid Tokens: ${source.session[0].slice(0, 4)} ${source.channel.slice(0, 4)} ||| ` +
        `${destination.session[0].slice(0, 4)} ${destination.channel.slice(0, 4)} ` +
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
        i: number): any {
        let chunk = new Uint8Array();
        if (i < n) {
          if (n === 1) {
            chunk = new Uint8Array([...[0, 0, 0, 0], ...payload]);
          } else {
            if (n > 2 ** 16) {
              throw `Payload size ${payload.length / 2 ** 20} MiB is too large`;
            }
            chunk = new Uint8Array([
              ...intToBytes(i, 2),
              ...intToBytes(n, 2),
              ...mid,
              ...payload.slice(i * maxPayload, (i + 1) * maxPayload)
            ]);
          }
          let nonce = webcrypto.getRandomValues(new Uint8Array(16));
          yield new Uint8Array([...nonce, ...(await sharedKey.encrypt(chunk, nonce) as Uint8Array)]);
          yield* await encryptSlice(payload, maxPayload, sharedKey, mid, n, i + 1);
        }
      }
      async function execute(channel: Channel, header: Header, sliceGenerator: any, mid: Uint8Array) {
        for await (let slice of sliceGenerator) {
          await channel.execute(header, slice, mid)
        }
      }
      let sourceRoute = this.route.clone();
      await this.session.extendRoute(sourceRoute, destination.session[0]);
      this.listen();

      let payload = new Uint8Array(serialize(payloadObj));

      if (payload.length < this.MAX_COMPRESSION_LEN) {
        const ff = deflate(payload);

        payload = new Uint8Array([255, ...ff]);

      } else {
        payload = new Uint8Array([0, ...payload]);
      }

      let mid = webcrypto.getRandomValues(new Uint8Array(4));
      // console.log(`>>> ${this.route} ${destination} ${Object.keys(payloadObj)} ${mid}`)

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
        await Promise.all(Array(nTasks).fill(0).map(() => new Promise((r, re) => { execute(this, header, gen, mid).then(r).catch(re) })))
      } catch (e) {
        this.session.clear(mid);
        throw e;
      }
      return new Promise(r => r(this));
    }
  }
  async execute(header?: Header, payload: Uint8Array = new Uint8Array(), bundleId?: Uint8Array) {
    if (this.route === undefined) {
      await new Promise(r => this.initLocks.push(r))
    }
    if (this.route !== undefined) {
      if (this.headerBuffer.length > 0 || header !== undefined) {
        let allHeaders = header !== undefined ? this.headerBuffer.concat([header]) : this.headerBuffer;
        let accLen = 1;
        let groups = [[]] as Header[][];


        for (let h of allHeaders) {
          let l = JSON.stringify(h).length
          if (accLen + l + 1 < 256**2) {
            groups[groups.length-1].push(h);
            accLen += l + 1;
          } else {
            groups.push([h])
            accLen = l + 2
          }
        }

        for (let headerGroup of groups) {
          await this.session.send(
            headerGroup,
            headerGroup.map(([a, _]) => a).includes('send') ? payload : undefined,
            bundleId)
          this.headerBuffer = [];

        }
      }
    }
    this.then = undefined;
    return new Promise(r => r(this));
  }
  _then(resolve: ((ret: any) => void)) {
    if (this.headerBuffer.length === 0 && this.route !== undefined) {
      this.then = undefined;
      resolve(this);
    } else {
      this.execute().then(() => resolve(this)).catch(e => console.log('Error in channel._then', e))
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
    let sessionId = await this.session.sessionKey.publicSerial(false)
    if (this.isPublic || (sourceId === sessionId)) { return true }
    if (tokens.length === 0) { return false }

    let asset = await this.channelKey.publicSerial(false);
    let lastReceiver = sessionId;
    let maxDepth = null;

    for (let depth in tokens) {
      let token;
      try {
        token = await Token.decode(tokens[depth]) as Token;
      } catch (e) {
        if ((e as Error).message === 'Invalid Signature') { return false }
        throw e;
      }
      if ((token.asset === asset) && (token.issuer === lastReceiver) && token.signature) {
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
  session: [string, string];
  channel: string;
  tokens: string[];
  _hash: string;
  _parentChannel?: Channel;

  constructor(brokers: string[], session: [string, string], channel: string, tokens: string[], parentChannel?: Channel) { // 
    this.brokers = brokers;
    this.session = session;
    this.channel = channel;
    this.tokens = tokens;
    this._hash = this.session.join('') + this.channel;
    this._parentChannel = parentChannel;
  }
  toObject() {
    return {
      brokers: this.brokers,
      session: this.session,
      channel: this.channel,
      tokens: this.tokens,
    }
  }
  toString() {
    return `Route ${this.session[0].slice(0, 4)} ${this.session[1].slice(0, 2)} - ${this.channel.slice(0, 4)}`
  }
  clone() {
    return Route.fromObject(this.toObject());
  }
  async validateTokenChain(receiver: string) {
    if (this.session[0] !== receiver) {
      if (!(this.tokens.length > 0)) {
        throw 'Invalid token chain 0';
      }
      let prevToken: Token | undefined = undefined;
      for (let i in this.tokens) {
        let token = await Token.decode(this.tokens[i]) as Token;
        prevToken = prevToken || token;
        if (
          (i === '0' && (!(token.asset == this.channel) || !(token.tokenType == 'root') || !(token.issuer == this.session[0]))) ||
          (i !== '0') && (!(token.issuer === prevToken.receiver) || !(token.asset === prevToken.signature) || !(token.tokenType === 'extension')) ||
          (i === this.tokens.length.toString() && token.receiver !== receiver)
        ) {
          // console.log(i, token, prevToken, this, receiver)
          // console.log(!(token.issuer === prevToken.receiver), !(token.asset === prevToken.signature) || !(token.tokenType === 'extension'), token.receiver !== receiver)
          throw 'Invalid token chain';
        }
        if (token.receiver === receiver) {
          break
        }
      }
    }
    return true;
  }
  static fromObject(obj: { brokers: string[], session: [string, string], channel: string, tokens: string[] }) {
    return new Route(obj.brokers, obj.session, obj.channel, obj.tokens)
  }
}

export class RequestMetadata {
  _session: Session;
  sessionPublicKey: string;
  caller?: Route;
  rawMessages?: {}[];
  replyTo?: Route;
  pipeline?: [string, any | string | [any[], {}]][];

  constructor(session: Session, caller?: Route, rawMessages?: {}[]) {
    this._session = session;
    this.sessionPublicKey = "";
    session.sessionKey.publicSerial(false).then((s: string) => { this.sessionPublicKey = s })
    this.caller = caller;
    this.rawMessages = rawMessages;
  }
}
