"use client";

import { useEffect, useState } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";

export default function AdminSystemPage() {
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [events, setEvents] = useState<any[]>([]);

  useEffect(() => {
    gatewayFetch("/admin/system/health").then(setHealth).catch(() => setHealth(null));
    gatewayFetch("/admin/system/events?limit=25")
      .then((response) => setEvents(response.items ?? []))
      .catch(() => setEvents([]));
  }, []);

  return (
    <AdminGuard>
      <main className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <section className="panel">
          <h2 className="mb-4 text-2xl font-semibold">System Health</h2>
          <pre className="overflow-x-auto text-xs text-white/80">{JSON.stringify(health, null, 2)}</pre>
        </section>
        <section className="panel">
          <h2 className="mb-4 text-2xl font-semibold">Recent Realtime Events</h2>
          <div className="space-y-3">
            {events.map((event, index) => (
              <pre key={`${event.event_id ?? index}-${index}`} className="overflow-x-auto rounded-2xl bg-black/20 p-3 text-xs text-white/80">
                {JSON.stringify(event, null, 2)}
              </pre>
            ))}
          </div>
        </section>
      </main>
    </AdminGuard>
  );
}
