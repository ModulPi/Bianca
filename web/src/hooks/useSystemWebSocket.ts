import { useEffect, useRef, useState } from "react";

export interface WsEvent {
  type: string;
  pending_id?: string;
  signal?: Record<string, unknown>;
  expires_at?: string;
  reason?: string;
  status?: string;
}

export function useSystemWebSocket(onEvent?: (event: WsEvent) => void) {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<WsEvent | null>(null);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/api/v1/ws/system`;
    let ws: WebSocket | null = null;
    let timer: number | undefined;

    const connect = () => {
      ws = new WebSocket(url);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        timer = window.setTimeout(connect, 3000);
      };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data as string) as WsEvent;
          setLastEvent(data);
          handlerRef.current?.(data);
        } catch {
          /* ignore */
        }
      };
    };

    connect();
    return () => {
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { connected, lastEvent };
}
