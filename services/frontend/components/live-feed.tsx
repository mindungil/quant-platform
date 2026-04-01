"use client";

import { useEffect, useState } from "react";
import { connectGatewaySocket } from "../lib/socket";

export function LiveFeed() {
  const [events, setEvents] = useState<string[]>([]);

  useEffect(() => {
    return connectGatewaySocket((payload) => {
      setEvents((current) => [JSON.stringify(payload), ...current].slice(0, 8));
    });
  }, []);

  return (
    <div className="panel">
      <h3 className="mb-3 text-lg font-semibold">Realtime Events</h3>
      <div className="space-y-2 text-sm text-white/80">
        {events.length === 0 ? <p>No events yet. Log in and wait for gateway pushes.</p> : null}
        {events.map((event, index) => (
          <pre key={`${event}-${index}`} className="overflow-x-auto rounded-2xl bg-black/20 p-3 text-xs">
            {event}
          </pre>
        ))}
      </div>
    </div>
  );
}
