import { useEffect, useState } from "react";
import { clientId } from "./api";
import type { Snapshot } from "./types";

export type LinkStatus = "connecting" | "live" | "down";

export function useReceiver(): {
  snapshot: Snapshot | null;
  ws: LinkStatus;
} {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [ws, setWs] = useState<LinkStatus>("connecting");

  useEffect(() => {
    let socket: WebSocket | null = null;
    let disposed = false;
    let retry = 0;
    let reconnectTimer: number | undefined;
    const heartbeat = window.setInterval(() => {
      socket?.send(JSON.stringify({ type: "hb" }));
    }, 20_000);

    const connect = () => {
      if (disposed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws/state?client_id=${encodeURIComponent(clientId())}`;
      socket = new WebSocket(url);
      socket.onopen = () => {
        retry = 0;
        setWs("live");
      };
      socket.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data as string) as Snapshot;
          if (data.type === "state") setSnapshot(data);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setWs("down");
        if (!disposed) {
          retry += 1;
          reconnectTimer = window.setTimeout(connect, Math.min(500 * retry, 5000));
        }
      };
    };
    connect();

    return () => {
      disposed = true;
      window.clearInterval(heartbeat);
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  return { snapshot, ws };
}