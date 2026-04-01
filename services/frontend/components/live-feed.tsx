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
    <div className="card">
      <h3 className="mb-3 text-lg font-semibold text-neutral-900">Realtime Events</h3>
      <div className="space-y-2 text-sm text-neutral-600">
        {events.length === 0 ? <p className="text-neutral-400">No events yet. Log in and wait for gateway pushes.</p> : null}
        {events.map((event, index) => (
          <pre key={`${event}-${index}`} className="overflow-x-auto rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-xs text-neutral-600">
            {event}
          </pre>
        ))}
      </div>
    </div>
  );
}
