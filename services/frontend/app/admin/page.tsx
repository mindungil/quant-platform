"use client";

import Link from "next/link";

import { AdminGuard } from "../../components/admin-guard";

export default function AdminPage() {
  return (
    <AdminGuard>
      <main className="grid gap-6 md:grid-cols-2">
        <section className="panel">
          <p className="text-sm uppercase tracking-[0.2em] text-mint">Admin</p>
          <h2 className="mt-2 text-3xl font-semibold">Operator Control Surface</h2>
          <p className="mt-3 text-white/75">
            Manage user roles, inspect service health, and review the global realtime event buffer from here.
          </p>
        </section>
        <section className="grid gap-4">
          <Link href="/admin/users" className="panel hover:bg-white/5">
            <h3 className="text-xl font-semibold">User Management</h3>
            <p className="mt-2 text-white/70">Review plans and roles, then promote or demote admin access.</p>
          </Link>
          <Link href="/admin/system" className="panel hover:bg-white/5">
            <h3 className="text-xl font-semibold">System Diagnostics</h3>
            <p className="mt-2 text-white/70">Inspect service health and replay-buffer events from the gateway.</p>
          </Link>
        </section>
      </main>
    </AdminGuard>
  );
}
