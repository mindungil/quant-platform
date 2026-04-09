"use client";

import { gatewayBase, getToken } from "./api";

export function connectGatewaySocket(onMessage: (payload: unknown) => void) {
  const token = getToken();
  if (!token) return () => {};

  const base = gatewayBase.replace(/^http/, "ws");
  let attempts = 0;
  let socket: WebSocket | null = null;
  let closed = false;
  const replayQueue: unknown[] = [];

  const flushReplayQueue = () => {
    while (replayQueue.length > 0) {
      const payload = replayQueue.shift();
      onMessage(payload);
    }
  };

  const connect = () => {
    socket = new WebSocket(`${base}/ws?token=${encodeURIComponent(token)}`);
    socket.onopen = () => {
      attempts = 0;
      flushReplayQueue();
    };
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      replayQueue.push(payload);
      if (replayQueue.length > 12) {
        replayQueue.shift();
      }
      flushReplayQueue();
    };
    socket.onclose = () => {
      if (closed) return;
      attempts += 1;
      const delay = Math.min(1000 * 2 ** attempts, 8000);
      window.setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    closed = true;
    socket?.close();
  };
}
