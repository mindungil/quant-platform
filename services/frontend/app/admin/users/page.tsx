"use client";

import { useEffect, useState } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  motion,
} from "../../../components/motion";

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
      <PageTransition>
        <main className="grid gap-6">
          {/* Header */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">USERS</p>
            <h2 className="mt-1 text-2xl font-semibold text-white">사용자 관리</h2>
            <p className="mt-2 text-neutral-500">
              사용자 계정 관리, 플랜 확인, 역할 할당
            </p>
            {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
          </section>

          {/* Stats */}
          <StaggerContainer className="grid gap-4 sm:grid-cols-3">
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">전체 사용자</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-white">
                  {users.length}
                </p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">관리자</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-white">
                  {adminCount}
                </p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">일반 사용자</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-white">
                  {users.length - adminCount}
                </p>
              </div>
            </StaggerItem>
          </StaggerContainer>

          {/* User Table */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-3 sm:p-6">
            <div className="-mx-3 sm:mx-0 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] text-xs font-medium uppercase tracking-wider text-neutral-400">
                  <th className="pb-3 pr-4 pl-3 sm:pl-0">사용자</th>
                  <th className="pb-3 pr-4">플랜</th>
                  <th className="pb-3 pr-4">역할</th>
                  <th className="pb-3 pr-4 hidden sm:table-cell">생성일</th>
                  <th className="pb-3 pr-3 sm:pr-0">작업</th>
                </tr>
              </thead>
              <tbody>
                {users.length > 0 ? (
                  users.map((user, index) => (
                    <motion.tr
                      key={user.user_id}
                      className="border-b border-white/[0.06]"
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.25, delay: index * 0.04, ease: "easeOut" }}
                    >
                      <td className="py-3 pr-4 pl-3 sm:pl-0 max-w-[200px]">
                        <p className="font-semibold text-white truncate">{user.display_name}</p>
                        <p className="text-xs text-neutral-400 truncate">{user.email}</p>
                      </td>
                      <td className="py-3 pr-4">
                        <span className="inline-flex items-center rounded-full bg-white/[0.06] px-2 py-0.5 text-xs font-medium text-neutral-400">
                          {user.plan}
                        </span>
                      </td>
                      <td className="py-3 pr-4">
                        <select
                          value={user.roles.includes("admin") ? "admin" : "user"}
                          onChange={(e) => updateRole(user, e.target.value)}
                          className="rounded border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-xs text-white outline-none focus:border-white/[0.30] transition-colors duration-200"
                        >
                          <option value="user">user</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>
                      <td className="py-3 pr-4 text-xs text-neutral-400 hidden sm:table-cell">
                        {user.created_at ? new Date(user.created_at).toLocaleDateString() : "--"}
                      </td>
                      <td className="py-3">
                        <span className="text-xs text-neutral-400">
                          {user.roles.join(", ")}
                        </span>
                      </td>
                    </motion.tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-neutral-400">
                      사용자를 찾을 수 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            </div>
          </section>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
