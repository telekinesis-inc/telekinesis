import { Connection, Session } from "./client";
import { Telekinesis } from "./telekinesis";

const btoa_ = typeof btoa !== 'undefined' ? btoa : require('btoa');
const atob_ = typeof atob !== 'undefined' ? atob : require('atob');

export function b64encode(u8: Uint8Array) {
  return btoa_(String.fromCharCode.apply(null, u8 as any));
}
export function b64decode(str: string) {
  return new Uint8Array(atob_(str).split('').map(function (c: string) { return c.charCodeAt(0); }));
}
export function intToBytes(int: number, len: number) {
  return new Uint8Array(len).map((_, i) => int / 256 ** (len - i - 1))
}
export function bytesToInt(bytes: Uint8Array) {
  return Array.from(bytes).reduce((p, c, i) => p + c * 256 ** (bytes.length - i - 1), 0)
}
export function eqSet(as: Set<any>, bs: Set<any>) {
    if (as.size !== bs.size) return false;
    for (var a of as) if (!bs.has(a)) return false;
    return true;
}

export function authenticate(url: string = 'ws://localhost:8776', sessionKey?: { privateKey: {}, publicKey: {} },
  printCallback: ((output: any) => void) = console.log, kwargs?: any) {

  let user = (new PublicUser(url, sessionKey) as any)
    .authenticate._call([printCallback], kwargs);

  return user;
}

export class PublicUser {
  constructor(url: string = 'ws://localhost:8776', sessionKey?: { privateKey: {}, publicKey: {} }, ...args: any) {
    if (!/(?![\w\d]+:\/\/[\w\d.]+):[\d]+/.exec(url)) {
      let i = (/[\w\d]+:\/\/[\w\d.]+/.exec(url) as any)[0].length;
      url = url.slice(0, i) + ':8776' + url.slice(i);
    }
    const session = new Session(sessionKey);
    const connection = new Connection(session, url);

    return new Telekinesis(new Promise((r: any) => connection.connect().then(() => r(connection.entrypoint))), session, ...args)
  }
}