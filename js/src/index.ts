global.crypto = require('crypto').webcrypto;
global.WebSocket = require('ws');
(global as any).isNode = true;

import { Channel, Connection, Route, Session } from './client';
import { PrivateKey, PublicKey, SharedKey, Token } from './cryptography';
import { authenticate, Entrypoint } from './helpers';
import { injectFirstArg, blockArgEvaluation, State, Telekinesis } from './telekinesis';


export {
  PrivateKey, PublicKey, SharedKey, Token, Connection, Session, Channel, Route, State,
  Telekinesis, injectFirstArg, blockArgEvaluation, Entrypoint, authenticate
};
