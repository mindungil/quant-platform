"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";

export default function StrategiesPage() {
  const [strategy, setStrategy] = useState<any | null>(null);

  useEffect(() => {
    gatewayFetch("/strategies").then(setStrategy).catch(() => setStrategy(null));
  }, []);

  return (
    <main className="panel">
      <h2 className="mb-4 text-2xl font-semibold">Strategies</h2>
      <pre className="overflow-x-auto text-sm text-white/80">{JSON.stringify(strategy, null, 2)}</pre>
    </main>
  );
}
