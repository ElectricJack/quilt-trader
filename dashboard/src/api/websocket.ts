type MessageHandler = (data: unknown) => void;

const RECONNECT_DELAY_MS = 3000;

export class WebSocketManager {
  private url: string;
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<MessageHandler>> = new Map();
  private _connected = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;

  constructor(url: string) {
    this.url = url;
  }

  connect(): void {
    this.shouldReconnect = true;
    this._open();
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._connected = false;
  }

  get connected(): boolean {
    return this._connected;
  }

  subscribe(event: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(handler);
    return () => {
      this.handlers.get(event)?.delete(handler);
    };
  }

  send(data: unknown): void {
    if (this.ws && this._connected) {
      this.ws.send(JSON.stringify(data));
    }
  }

  private _open(): void {
    if (this.ws) return;

    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this._connected = true;
    };

    this.ws.onmessage = (event: MessageEvent) => {
      let data: unknown;
      try {
        data = JSON.parse(event.data as string);
      } catch {
        data = event.data;
      }

      const msgType =
        data !== null &&
        typeof data === "object" &&
        "type" in data &&
        typeof (data as Record<string, unknown>).type === "string"
          ? (data as Record<string, unknown>).type as string
          : null;

      if (msgType) {
        this.handlers.get(msgType)?.forEach((h) => h(data));
      }
      // wildcard
      this.handlers.get("*")?.forEach((h) => h(data));
    };

    this.ws.onclose = () => {
      this._connected = false;
      this.ws = null;
      if (this.shouldReconnect) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      // onclose will fire after onerror; reconnect handled there
    };
  }

  private _scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.shouldReconnect) {
        this._open();
      }
    }, RECONNECT_DELAY_MS);
  }
}

const wsUrl =
  typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`
    : "ws://localhost:8000/ws";

export const wsManager = new WebSocketManager(wsUrl);
