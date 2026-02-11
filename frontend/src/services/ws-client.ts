/** WebSocket client for real-time vehicle updates. */

export interface VehicleData {
  id: string;
  board_num: string;
  route: string;
  route_id: number | null;
  lat: number;
  lon: number;
  speed: number;
  course: number;
  prev_stop: { id: number; name: string } | null;
  next_stops: Array<{ id: number; name: string; eta_seconds: number | null }>;
  progress: number | null;
  timestamp: string | null;
  signal_lost: boolean;
}

export interface WsMessage {
  type: "snapshot" | "update";
  vehicles: VehicleData[];
}

type Listener = (msg: WsMessage) => void;

export class WsClient {
  private ws: WebSocket | null = null;
  private listeners: Set<Listener> = new Set();
  private reconnectTimeout: number | null = null;
  private url: string;

  onStatusChange: ((connected: boolean) => void) | null = null;

  constructor() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${proto}//${location.host}/ws/vehicles`;
  }

  connect(): void {
    if (this.ws) return;

    this.ws = new WebSocket(this.url);
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      console.log("[WS] Connected");
      this.onStatusChange?.(true);
    };

    this.ws.onmessage = (ev) => {
      try {
        const data: ArrayBuffer = ev.data;
        const text = new TextDecoder().decode(data);
        const msg: WsMessage = JSON.parse(text);
        for (const listener of this.listeners) {
          listener(msg);
        }
      } catch (e) {
        console.error("[WS] Parse error:", e);
      }
    };

    this.ws.onclose = () => {
      console.log("[WS] Disconnected, reconnecting in 3s...");
      this.ws = null;
      this.onStatusChange?.(false);
      this.reconnectTimeout = window.setTimeout(() => this.connect(), 3000);
    };

    this.ws.onerror = (err) => {
      console.error("[WS] Error:", err);
      this.ws?.close();
    };
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  disconnect(): void {
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.ws?.close();
    this.ws = null;
  }
}
