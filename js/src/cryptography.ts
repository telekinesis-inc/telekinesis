import { b64encode, b64decode } from "./helpers"

export class PrivateKey {
  _algorithm: "ECDSA" | "ECDH";
  _usage: ["sign", "verify"] | ["deriveKey"];
  key?: CryptoKeyPair;

  constructor(use: "sign" | "derive") {
    if (use === 'sign') {
      this._algorithm = 'ECDSA'
      this._usage = ['sign', 'verify']
      return
    }
    this._algorithm = 'ECDH'
    this._usage = ['deriveKey']
    return
  }
  async generate() {
    this.key = await crypto.subtle.generateKey({
        name: this._algorithm,
        namedCurve: "P-256"
      },
      true,
      this._usage
    )
    return this
  }
  async sign(message: Uint8Array) {
    if (this.key === undefined) {
      await this.generate()
    }
    if (this.key !== undefined) {
      let signatureBuf = await crypto.subtle.sign(
        {
          name: "ECDSA",
          hash: {name: "SHA-256"},
        },
        this.key.privateKey,
        message
      )
      return new Uint8Array(signatureBuf)
    }
  }
  async publicSerial() {
    if (this.key === undefined) {
      await this.generate()
    }
    if (this.key !== undefined) {
      let publicKey = (await crypto.subtle.exportKey('raw', this.key.publicKey)).slice(1)
      return b64encode(new Uint8Array(publicKey))
    }
  }
}
export class PublicKey {
  _algorithm: "ECDSA" | "ECDH";
  _usage: ["verify"] | [];
  publicSerial: string;
  key?: CryptoKey;

  constructor(use: "verify" | "derive" , publicSerial: string) {
    this.publicSerial = publicSerial
    if (use === 'verify') {
      this._algorithm = 'ECDSA'
      this._usage = ['verify']
      return
    } 
    if (use === 'derive') {
      this._algorithm = 'ECDH'
      this._usage = []
      return
    }
    throw "expected input 'use' should be 'verify' or 'derive'"
    
  }
  async generate() {
    let decodedPublicSerial = b64decode(this.publicSerial)
    this.key = await crypto.subtle.importKey(
      'raw',
      new Uint8Array([4, ...decodedPublicSerial]).buffer,
      {
        name: this._algorithm,
        namedCurve: 'P-256'
      },
      true,
      this._usage
    )
    return this
  }
  async verify(signature: Uint8Array, message: Uint8Array) {
    if (this.key === undefined) {
      await this.generate()
    }
    if (this.key !== undefined) {
      if (await crypto.subtle.verify(
        {
          name: "ECDSA",
          hash: {name: "SHA-256"},
        },
        this.key,
        signature,
        message
      )) { return; } else { throw 'Invalid Signature';}
    }
  }
}
export class SharedKey {
  publicSerial: string;
  privateKey: PrivateKey;
  key?: CryptoKey;

  constructor(privateKey: PrivateKey, publicSerial: string) {
    this.publicSerial = publicSerial
    this.privateKey = privateKey
  }
  async generate() {
    if (this.privateKey.key === undefined) {
      await this.privateKey.generate();
    }
    if (this.privateKey.key !== undefined) {
      this.key = await crypto.subtle.deriveKey(
        {
          name: "ECDH",
          public: (await new PublicKey('derive', this.publicSerial).generate()).key
        },
        this.privateKey.key.privateKey,
        {
          name: "AES-CTR",
          length: 256
        },
        false,
        ['encrypt', 'decrypt']
      );
      return this;
    }
  }
  async encrypt(message: Uint8Array, nonce: Uint8Array) {
    if (this.key === undefined) {
      await this.generate()
    }
    if (this.key !== undefined) {
      let out = await crypto.subtle.encrypt(
        {
          name: "AES-CTR",
          counter: nonce,
          length: 128
        },
        this.key,
        message
      )
      return new Uint8Array(out);
    }
  }
  async decrypt(ciphertext: Uint8Array, nonce: Uint8Array) {
    if (this.key === undefined) {
      await this.generate()
    }
    if (this.key !== undefined) {
      return await crypto.subtle.encrypt(
        {
          name: "AES-CTR",
          counter: nonce,
          length: 128
        },
        this.key,
        ciphertext
      )
    }
  }
}
export class Token {
  issuer: string;
  brokers: string[];
  receiver: string;
  asset: string;
  tokenType: "root"|"extension";
  maxDepth: number | null;
  validFrom: number;
  validUntil: number | null;
  failMode: 'CLOSED'|'OPEN';
  metadata: {};
  signature?: string;
  
  constructor(
    issuer: string, brokers: string[], receiver: string, asset: string, tokenType: 'root'|'extension', maxDepth?: number,
    validFrom?: number, validUntil?: number, failMode: 'CLOSED'|'OPEN' = 'CLOSED', metadata: {} = {}) {
    this.issuer = issuer;
    this.brokers = brokers;
    this.receiver = receiver;
    this.asset = asset;
    this.tokenType = tokenType;
    this.maxDepth = maxDepth || null;
    this.validFrom = validFrom || Date.now()/1000;
    this.validUntil = validUntil || null;
    this.failMode = failMode;
    this.metadata = metadata;

  }
  async verify(signature: string) {
    try {
      await new PublicKey('verify', this.issuer).verify(
        b64decode(signature),
        new TextEncoder().encode(this.toString()));
      this.signature = signature;
      return true;
    } catch {
      return false;
    }
  }
  toString() {
    return JSON.stringify(this.toObj())
  }
  toObj() {
    return {
      issuer: this.issuer,
      brokers: this.brokers,
      receiver: this.receiver,
      asset: this.asset,
      token_type: this.tokenType,
      max_depth: this.maxDepth,
      valid_from: this.validFrom,
      valid_until: this.validUntil,
      fail_mode: this.failMode,
      metadata: this.metadata,
    }
  }
  encode() {
    if (this.signature === undefined) {
      throw 'You need to sign the token before encoding it';
    }
    return this.signature + '.' + this.toString();
  }
  async sign(signingKey: PrivateKey) {
    this.signature = b64encode(await signingKey.sign((new TextEncoder().encode(this.toString()))) as Uint8Array);

    return this.signature
  }
  static async decode(encodedToken: string, verify: boolean = true) {
    let obj = JSON.parse(encodedToken.substr(encodedToken.indexOf('.') + 1));
    let token = new Token(obj.issuer, obj.brokers, obj.receiver, obj.asset, obj.token_type, obj.max_depth, obj.valid_from,
      obj.valid_until, obj.fail_mode, obj.metadata);
    let signature = encodedToken.substring(0, encodedToken.indexOf('.'));
    if (!verify) {
      token.signature = signature;
      return token;
    }
    if (await token.verify(signature)) {
      return token;
    }
    console.error('Invalid Signature');
  }
}