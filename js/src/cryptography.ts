import { b64decode, b64encode } from "./utils";

const webcrypto = typeof crypto == 'undefined' ? global.crypto : crypto;

export class PrivateKey {
  _algorithm: "ECDSA" | "ECDH";
  _usage: ["sign"] | ["deriveKey"];
  repr?: string;
  key?: CryptoKeyPair;
  exportedKey?: string;
  name?: string;

  constructor(use: "sign" | "derive", importedKey?: string, name?: string) {
    this.exportedKey = importedKey;
    this.name = importedKey ? importedKey.split('-----END PRIVATE KEY-----').slice(1).join('-----END PRIVATE KEY-----').replace('\n', ''): name;
    if (use === 'sign') {
      this._algorithm = 'ECDSA';
      this._usage = ['sign'];
      return;
    }
    this._algorithm = 'ECDH';
    this._usage = ['deriveKey'];
    // this.publicSerial(); // <- don't do this!
    return
  }
  async generate() {
    if (this.exportedKey !== undefined) {
      let privateKey, jwk;
      privateKey = await webcrypto.subtle.importKey(
        "pkcs8",
        b64decode(this.exportedKey.split('-----END PRIVATE KEY-----')[0].split('\n').slice(1).join('')),
        {
          name: this._algorithm,
          namedCurve: "P-256"
        },
        true,
        this._usage
      );
      jwk = await webcrypto.subtle.exportKey("jwk", privateKey);
      this.key = {
        privateKey: privateKey,
        publicKey: await webcrypto.subtle.importKey(
          'jwk',
          {alg: jwk.alg, crv: jwk.crv, ext: true, key_ops: [], kty: jwk.kty, x: jwk.x, y: jwk.y},
          {
            name: this._algorithm,
            namedCurve: "P-256"
          },
          true,
          []
        )
      }
    } else {
      this.key = await webcrypto.subtle.generateKey({
        name: this._algorithm,
        namedCurve: "P-256"
      },
        true,
        this._usage
      )
    }
    return this
  }
  async sign(message: Uint8Array) {
    if (this.key === undefined) {
      await this.generate();
    }
    if (this.key !== undefined) {
      let signatureBuf = await webcrypto.subtle.sign(
        {
          name: "ECDSA",
          hash: { name: "SHA-256" },
        },
        this.key.privateKey as CryptoKey,
        message
      )
      return new Uint8Array(signatureBuf)
    }
    throw new Error('key is undefined');
  }
  async publicSerial(showName: boolean=true) {
    if (this.key === undefined) {
      await this.generate();
    }
    if (this.key !== undefined) {
      let publicKey = (await webcrypto.subtle.exportKey('raw', this.key.publicKey as CryptoKey)).slice(1);
      let out = (this.name && showName ? `${this.name}.` : '') + b64encode(new Uint8Array(publicKey));
      if (this.repr === undefined) {
        this.repr = out;
      }
      return out;
    }
    throw new Error('key is undefined')
  }
  async exportKey() {
    if (this.exportedKey == undefined) {
      if (this.key === undefined) {
        await this.generate();
      }
      if (this.key !== undefined) {
        this.exportedKey = 
          "-----BEGIN PRIVATE KEY-----\n" +
          b64encode(new Uint8Array(await webcrypto.subtle.exportKey("pkcs8", this.key.privateKey as CryptoKey))) +
          "\n-----END PRIVATE KEY-----" + (this.name? `\n${this.name}` : '')
      } else {
        throw new Error('key is undefined');
      }
    }
    return this.exportedKey;
  }
}
export class PublicKey {
  _algorithm: "ECDSA" | "ECDH";
  _usage: ["verify"] | [];
  publicSerial: string;
  key?: CryptoKey;
  name?: string;

  constructor(use: "verify" | "derive", publicSerial: string) {
    this.publicSerial = publicSerial
    this.name = publicSerial.split('.').slice(0, -1).join('.')
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
    throw new Error("expected input 'use' should be 'verify' or 'derive'");

  }
  async generate() {
    let decodedPublicSerial = b64decode(this.publicSerial.split('\n').slice(-1)[0])
    this.key = await webcrypto.subtle.importKey(
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
      if (await webcrypto.subtle.verify(
        {
          name: "ECDSA",
          hash: { name: "SHA-256" },
        },
        this.key,
        signature,
        message
      )) { return; } else { throw new Error('Invalid Signature'); }
    }
    throw new Error('key is undefined');
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
      this.key = await webcrypto.subtle.deriveKey(
        {
          name: "ECDH",
          public: (await new PublicKey('derive', this.publicSerial).generate()).key
        },
        this.privateKey.key.privateKey as CryptoKey,
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
      let out = await webcrypto.subtle.encrypt(
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
      return await webcrypto.subtle.encrypt(
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
  tokenType: "root" | "extension";
  maxDepth: number | null;
  validFrom: number;
  validUntil: number | null;
  metadata: {};
  signature?: string;

  constructor(
    issuer: string, brokers: string[], receiver: string, asset: string, tokenType: 'root' | 'extension', maxDepth?: number,
    validFrom?: number, validUntil?: number, metadata: {} = {}) {
    this.issuer = issuer;
    this.brokers = brokers;
    this.receiver = receiver;
    this.asset = asset;
    this.tokenType = tokenType;
    this.maxDepth = maxDepth || null;
    this.validFrom = validFrom || Date.now() / 1000;
    this.validUntil = validUntil || null;
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
      obj.valid_until, obj.metadata);
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