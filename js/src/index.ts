try { if (!global.crypto) (global as any).crypto = require('crypto').webcrypto; } catch {}
try { if (!(global as any).WebSocket) (global as any).WebSocket = require('ws'); } catch {}
(global as any).isNode = true;

import { Channel, Connection, Route, Session } from './client';
import { PrivateKey, PublicKey, SharedKey, Token } from './cryptography';
import { authenticate, Entrypoint } from './helpers';
import { injectFirstArg, blockArgEvaluation, State, Telekinesis } from './telekinesis';
import { FakeWebSocket, createWebSocket } from './fakeWebSocket';

import { deserialize, serialize } from "bson";
import { deflate, inflate } from "pako";

const utils = {deserialize, serialize, deflate, inflate};

export {
  PrivateKey, PublicKey, SharedKey, Token, Connection, Session, Channel, Route, State,
  Telekinesis, injectFirstArg, blockArgEvaluation, Entrypoint, authenticate, utils,
  FakeWebSocket, createWebSocket,
};
