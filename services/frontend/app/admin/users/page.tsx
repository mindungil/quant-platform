"use client";

import { useEffect, useState } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";

type UserProfile = {
  user_id: string;
  email: string;
  display_name: string;
  plan: string;
  roles: string[];
  automation_enabled: boolean;
};

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [error, setError] = useState("");

  async function loadUsers() {
    try {
      const response = await gatewayFetch("/admin/users");
      setUsers(response);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed_to_load_users");
    }
  }

  useEffect(() => {
    loadUsers();
  }, []);

  async function toggleAdmin(user: UserProfile) {
    const nextRoles = user.roles.includes("admin") ? ["user"] : ["user", "admin"];
    await gatewayFetch(`/admin/users/${user.user_id}/roles`, {
      method: "PATCH",
      body: JSON.stringify({ roles: nextRoles })
    });
    await loadUsers();
  }

  return (
    <AdminGuard>
      <main className="grid gap-6">
        <section className="panel">
          <h2 className="text-3xl font-semibold">User Roles</h2>
          <p className="mt-2 text-white/70">Bootstrap admin and tenant roles are managed at the gateway boundary.</p>
          {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
        </section>
        <section className="grid gap-4">
          {users.map((user) => (
            <article key={user.user_id} className="panel flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-lg font-semibold">{user.display_name}</p>
                <p className="text-sm text-white/60">{user.email}</p>
                <p className="mt-2 text-sm text-white/70">
                  Plan: <span className="text-mint">{user.plan}</span> | Roles: {user.roles.join(", ")}
                </p>
              </div>
              <button
                className="rounded-full border border-white/20 px-4 py-2 hover:bg-white/10"
                onClick={() => toggleAdmin(user)}
              >
                {user.roles.includes("admin") ? "Revoke Admin" : "Promote Admin"}
              </button>
            </article>
          ))}
        </section>
      </main>
    </AdminGuard>
  );
}
