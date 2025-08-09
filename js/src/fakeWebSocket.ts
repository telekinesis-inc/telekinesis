/**
 * Fake WebSocket implementation using HTTP long-polling as fallback.
 * Provides WebSocket-compatible API for browser and Node.js environments.
 */

// Environment detection
const isNode = typeof global !== 'undefined' && (global as any)?.isNode === true;

// HTTP client abstraction for Node.js vs Browser
interface HttpClient {
  request(method: string, url: string, headers?: Record<string, string>, body?: Uint8Array): Promise<{
    status: number;
    body: Uint8Array;
  }>;
}

class NodeHttpClient implements HttpClient {
  async request(method: string, url: string, headers: Record<string, string> = {}, body?: Uint8Array) {
    const http = await import('http');
    const https = await import('https');
    const urlLib = await import('url');
    
    const parsedUrl = new urlLib.URL(url);
    const isHttps = parsedUrl.protocol === 'https:';
    const client = isHttps ? https : http;
    
    return new Promise<{ status: number; body: Uint8Array }>((resolve, reject) => {
      const options = {
        method,
        hostname: parsedUrl.hostname,
        port: parsedUrl.port || (isHttps ? 443 : 80),
        path: parsedUrl.pathname + parsedUrl.search,
        headers: {
          'Content-Length': body ? body.length.toString() : '0',
          ...headers,
        },
      };

      const req = client.request(options, (res) => {
        const chunks: Uint8Array[] = [];
        res.on('data', (chunk) => {
          chunks.push(new Uint8Array(chunk));
        });
        res.on('end', () => {
          const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
          const result = new Uint8Array(totalLength);
          let offset = 0;
          for (const chunk of chunks) {
            result.set(chunk, offset);
            offset += chunk.length;
          }
          resolve({ status: res.statusCode || 0, body: result });
        });
      });

      req.on('error', reject);
      
      if (body) {
        req.write(Buffer.from(body));
      }
      req.end();
    });
  }
}

class BrowserHttpClient implements HttpClient {
  async request(method: string, url: string, headers: Record<string, string> = {}, body?: Uint8Array) {
    const response = await fetch(url, {
      method,
      headers,
      body: body ? body : undefined,
    });

    const arrayBuffer = await response.arrayBuffer();
    return {
      status: response.status,
      body: new Uint8Array(arrayBuffer),
    };
  }
}

const httpClient: HttpClient = isNode ? new NodeHttpClient() : new BrowserHttpClient();

export class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  public readyState: number = FakeWebSocket.CONNECTING;
  public onopen?: (event: Event) => void;
  public onmessage?: (event: MessageEvent) => void;
  public onclose?: (event: CloseEvent) => void;
  public onerror?: (event: any) => void;

  private url: string;
  private connectionId?: string;
  private baseUrl: string;
  private pollInterval: number = 100; // ms
  private pollTimeout: number = 30000; // ms
  private pollAbortController?: AbortController;
  private messageQueue: Uint8Array[] = [];
  private isPolling: boolean = false;

  constructor(url: string) {
    this.url = url;
    
    // Convert ws:// or wss:// to http:// or https://
    if (url.startsWith('ws://')) {
      this.baseUrl = 'http://' + url.slice(5);
    } else if (url.startsWith('wss://')) {
      this.baseUrl = 'https://' + url.slice(6);
    } else {
      this.baseUrl = url;
    }

    // Start connection process
    setTimeout(() => this.connect(), 0);
  }

  private async connect(): Promise<void> {
    try {
      console.log(`FakeWebSocket connecting to ${this.baseUrl}`);
      
      // Establish connection by POST to /connect
      const response = await httpClient.request('POST', `${this.baseUrl}/connect`);
      
      if (response.status === 200) {
        const responseText = new TextDecoder().decode(response.body);
        const responseData = JSON.parse(responseText);
        this.connectionId = responseData.connection_id;
        
        this.readyState = FakeWebSocket.OPEN;
        console.log(`FakeWebSocket connected with ID: ${this.connectionId}`);
        
        // Start polling for messages
        this.startPolling();
        
        // Trigger onopen event
        if (this.onopen) {
          this.onopen(new Event('open'));
        }
      } else {
        throw new Error(`Connection failed with status ${response.status}`);
      }
    } catch (error) {
      console.error('FakeWebSocket connection failed:', error);
      this.readyState = FakeWebSocket.CLOSED;
      if (this.onerror) {
        const errorEvent = { type: 'error', message: String(error) } as any;
        this.onerror(errorEvent);
      }
    }
  }

  private async startPolling(): Promise<void> {
    if (this.isPolling || this.readyState !== FakeWebSocket.OPEN || !this.connectionId) {
      return;
    }

    this.isPolling = true;
    
    while (this.readyState === FakeWebSocket.OPEN && this.connectionId) {
      try {
        this.pollAbortController = new AbortController();
        
        const response = await httpClient.request(
          'GET',
          `${this.baseUrl}/poll?connection_id=${this.connectionId}`
        );

        if (response.status === 200 && response.body.length > 0) {
          // Got a message
          console.log(`FakeWebSocket received ${response.body.length} bytes`);
          if (this.onmessage) {
            // Create a MessageEvent-like object with the binary data
            const messageEvent = {
              data: isNode ? response.body : new Blob([response.body]),
              type: 'message',
            } as MessageEvent;
            this.onmessage(messageEvent);
          }
        } else if (response.status === 204) {
          // No content - timeout, continue polling
          console.log('FakeWebSocket poll timeout, continuing...');
        } else if (response.status === 400) {
          // Connection invalid - likely closed
          console.log('FakeWebSocket connection invalid, closing...');
          this.close();
          break;
        } else {
          console.warn(`FakeWebSocket poll unexpected status: ${response.status}`);
          await new Promise(resolve => setTimeout(resolve, this.pollInterval));
        }
      } catch (error) {
        if (this.readyState === FakeWebSocket.OPEN) {
          // Only log if not in test environment or if it's not a connection refused error
          if (!process.env.NODE_ENV?.includes('test') && !(error as any).code?.includes('ECONNREFUSED')) {
            console.error('FakeWebSocket polling error:', error);
          }
          await new Promise(resolve => setTimeout(resolve, this.pollInterval));
        }
      }
    }
    
    this.isPolling = false;
  }

  public send(data: ArrayBuffer | Uint8Array | string): void {
    if (this.readyState !== FakeWebSocket.OPEN || !this.connectionId) {
      throw new Error('WebSocket is not open');
    }

    let binaryData: Uint8Array;
    if (typeof data === 'string') {
      binaryData = new TextEncoder().encode(data);
    } else if (data instanceof ArrayBuffer) {
      binaryData = new Uint8Array(data);
    } else {
      binaryData = data;
    }

    console.log(`FakeWebSocket sending ${binaryData.length} bytes`);

    // Send data asynchronously
    this.sendAsync(binaryData).catch(error => {
      console.error('FakeWebSocket send error:', error);
      if (this.onerror) {
        const errorEvent = { type: 'error', message: String(error) } as any;
        this.onerror(errorEvent);
      }
    });
  }

  private async sendAsync(data: Uint8Array): Promise<void> {
    if (!this.connectionId) {
      throw new Error('No connection ID');
    }

    try {
      const response = await httpClient.request(
        'POST',
        `${this.baseUrl}/send?connection_id=${this.connectionId}`,
        { 'Content-Type': 'application/octet-stream' },
        data
      );

      if (response.status !== 200) {
        throw new Error(`Send failed with status ${response.status}`);
      }
    } catch (error) {
      // Handle connection errors
      if (String(error).includes('400') || String(error).includes('Connection closed')) {
        this.close();
      }
      throw error;
    }
  }

  public close(code?: number, reason?: string): void {
    if (this.readyState === FakeWebSocket.CLOSED) {
      return;
    }

    console.log(`FakeWebSocket closing connection ${this.connectionId}`);
    this.readyState = FakeWebSocket.CLOSING;

    // Stop polling
    if (this.pollAbortController) {
      this.pollAbortController.abort();
    }

    // Send close request asynchronously
    if (this.connectionId) {
      httpClient.request(
        'POST',
        `${this.baseUrl}/close?connection_id=${this.connectionId}`
      ).catch(error => {
        console.warn('Error sending close request:', error);
      });
    }

    this.readyState = FakeWebSocket.CLOSED;
    this.connectionId = undefined;

    // Trigger onclose event
    if (this.onclose) {
      this.onclose(new CloseEvent('close', { code: code || 1000, reason: reason || '' }));
    }
  }
}

/**
 * Factory function to create appropriate WebSocket implementation
 * based on URL protocol (ws/wss vs http/https).
 */
export function createWebSocket(url: string): WebSocket | FakeWebSocket {
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return new FakeWebSocket(url) as any;
  } else {
    // Use native WebSocket for ws:// and wss:// URLs
    const WS = isNode ? global.WebSocket : WebSocket;
    return new WS(url) as WebSocket;
  }
}