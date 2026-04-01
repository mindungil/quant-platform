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
  created_at?: string;
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

  async function updateRole(user: UserProfile, newRole: string) {
    const nextRoles = newRole === "admin" ? ["user", "admin"] : ["user"];
    try {
      await gatewayFetch(`/admin/users/${user.user_id}/roles`, {
        method: "PATCH",
        body: JSON.stringify({ roles: nextRoles }),
      });
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "role_update_failed");
    }
  }

  const adminCount = users.filter((u) => u.roles.includes("admin")).length;

  return (
    <AdminGuard>
      <main className="grid gap-6">
        {/* Header */}
        <section className="panel">
          <h2 className="text-3xl font-semibold">User Management</h2>
          <p className="mt-2 text-white/70">
            Manage user accounts, review plans, and assign roles.
          </p>
          {error && <p className="mt-3 text-sm text-red-300">{error}</p>}
        </section>

        {/* Stats */}
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="panel text-center">
            <p className="text-sm text-white/60">Total Users</p>
            <p className="mt-1 text-3xl font-bold text-mint">{users.length}</p>
          </div>
          <div className="panel text-center">
            <p className="text-sm text-white/60">Admins</p>
            <p className="mt-1 text-3xl font-bold text-sand">{adminCount}</p>
          </div>
          <div className="panel text-center">
            <p className="text-sm text-white/60">Regular Users</p>
            <p className="mt-1 text-3xl font-bold text-white/80">{users.length - adminCount}</p>
          </div>
        </div>

        {/* User Table */}
        <section className="panel overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-xs uppercase tracking-wider text-white/50">
                <th className="pb-3 pr-4">User</th>
                <th className="pb-3 pr-4">Plan</th>
                <th className="pb-3 pr-4">Role</th>
                <th className="pb-3 pr-4">Created</th>
                <th className="pb-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.user_id} className="border-b border-white/5">
                  <td className="py-3 pr-4">
                    <p className="font-semibold">{user.display_name}</p>
                    <p className="text-xs text-white/50">{user.email}</p>
                  </td>
                  <td className="py-3 pr-4">
                    <span className="rounded-full bg-mint/20 px-3 py-1 text-xs font-medium text-mint">
                      {user.plan}
                    </span>
                  </td>
                  <td className="py-3 pr-4">
                    <select
                      value={user.roles.includes("admin") ? "admin" : "user"}
                      onChange={(e) => updateRole(user, e.target.value)}
                      className="rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-xs text-white/90"
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="py-3 pr-4 text-xs text-white/50">
                    {user.created_at ? new Date(user.created_at).toLocaleDateString() : "--"}
                  </td>
                  <td className="py-3">
                    <span className="text-xs text-white/40">
                      {user.roles.join(", ")}
                    </span>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-white/50">
                    No users found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </main>
    </AdminGuard>
  );
}
