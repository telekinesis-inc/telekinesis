import { Connection, Session, Channel, Route} from "./client";
import { Telekinesis } from "./telekinesis";

export function b64encode(u8: Uint8Array) {
  return btoa(String.fromCharCode.apply(null, u8 as any));
}

export function b64decode(str: string) {
  return new Uint8Array(atob(str).split('').map(function (c) { return c.charCodeAt(0); }));
}
export function intToBytes(int: number, len: number) {
  return new Uint8Array(len).map((_, i) => int/256**(len-i-1))
}
export function bytesToInt(bytes: Uint8Array) {
  return Array.from(bytes).reduce((p, c, i) => p+c*256**(bytes.length-i-1), 0)
}

export async function authenticate(url: string, printCallback: ((output: any) => void) = console.log) {
  let s = new Session();
  let c = new Connection(s, url);
  await c.connect();

  let endpoint = new Telekinesis(c.endpoint as Route, s);

  let user = await endpoint._call(printCallback, ...Array.from(arguments).slice(2,));

  if (!user) {
    throw 'Failed to authenticate';
  }
  return user;
}