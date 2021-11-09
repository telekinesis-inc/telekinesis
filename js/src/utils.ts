// const btoa_ = global.btoa;
// const atob_ = global.atob;

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
