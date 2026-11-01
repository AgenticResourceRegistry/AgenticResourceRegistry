// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Anupam Kumar <anupam9594.kumar@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useState, useCallback } from "react";
import { Users, Plus, Copy, Check, Loader2, Key, Trash2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { useAdminUsers, useCreateUser, useUpdateUserRole, useUpdateUserDepartment, useDeleteUser, useResetPassword } from "@/hooks/use-api";
import { admin } from "@/lib/api";
import type { AdminUser } from "@/lib/types";
import { copyToClipboard } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { PickerSelect } from "@/components/ui/picker-select";
import { PageHeader } from "@/components/layouts/page-header";
import { AdminPanel } from "./components/admin-surface";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { ErrorState } from "@/components/shared/error-state";
import { EmptyState } from "@/components/shared/empty-state";
import { ROLE_LABELS, hasMinRole, type Role } from "@/hooks/use-role-guard";
import { getUserRole } from "@/lib/api";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";

const ALL_ROLES: Role[] = ["super_admin", "admin", "reviewer", "user"];

function initials(name: string | null | undefined, email: string | null | undefined) {
  const source = name?.trim() || email?.split("@")[0] || "User";
  return source.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function useAssignableRoles(): Role[] {
  const myRole = getUserRole();
  return ALL_ROLES.filter((r) => hasMinRole(myRole, r));
}

function RoleSelect({ userId, currentRole }: { userId: string; currentRole: string }) {
  const mutation = useUpdateUserRole();
  const assignable = useAssignableRoles();
  const currentRoleOption = {
    value: currentRole,
    label: ROLE_LABELS[currentRole as Role] ?? currentRole,
  };
  const canEdit = assignable.includes(currentRole as Role);
  const options = canEdit
    ? assignable.map((r) => ({ value: r, label: ROLE_LABELS[r] }))
    : [currentRoleOption];

  return (
    <PickerSelect
      value={currentRole}
      onValueChange={(value) => mutation.mutate({ id: userId, role: value })}
      disabled={!canEdit || mutation.isPending}
      className="w-[140px]"
      inputClassName="h-7 text-xs"
      options={options}
    />
  );
}

function DepartmentInput({ userId, currentDept }: { userId: string; currentDept: string | null | undefined }) {
  const mutation = useUpdateUserDepartment();
  const [value, setValue] = useState(currentDept ?? "");
  const [editing, setEditing] = useState(false);

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        title="Click to set department"
      >
        {currentDept || "—"}
      </button>
    );
  }

  return (
    <Input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        const trimmed = value.trim() || null;
        if (trimmed !== (currentDept ?? null)) {
          mutation.mutate({ id: userId, department: trimmed });
        }
        setEditing(false);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") { setValue(currentDept ?? ""); setEditing(false); }
      }}
      className="h-6 w-[120px] text-xs px-1.5"
      placeholder="Department"
      autoFocus
    />
  );
}

export default function UsersPage() {
  const { data: users, isLoading, isError, error, refetch } = useAdminUsers();
  const createUser = useCreateUser();
  const deleteUser = useDeleteUser();
  const resetPassword = useResetPassword();
  const assignableRoles = useAssignableRoles();
  const { ssoOnly } = useDeploymentConfig();
  const [showCreate, setShowCreate] = useState(false);
  const [showBulkDept, setShowBulkDept] = useState(false);
  const [bulkCsv, setBulkCsv] = useState("");
  const [bulkLoading, setBulkLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [resetResult, setResetResult] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<string>("user");
  const [createdPassword, setCreatedPassword] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [resetCopied, setResetCopied] = useState(false);

  const handleCreate = useCallback(async () => {
    if (!name.trim() || !email.trim()) return;
    createUser.mutate(
      { email: email.trim(), name: name.trim(), username: username.trim() || undefined, role },
      {
        onSuccess: (data) => {
          setCreatedPassword(data.password);
          setName("");
          setEmail("");
          setUsername("");
          setRole("user");
        },
      },
    );
  }, [name, email, username, role, createUser]);

  const handleCopyPassword = useCallback(async () => {
    if (!createdPassword) return;
    try {
      await copyToClipboard(createdPassword);
      setCopied(true);
      toast.success("Password copied");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy password");
    }
  }, [createdPassword]);

  const handleResetPassword = useCallback((user: AdminUser) => {
    resetPassword.mutate(user.id, {
      onSuccess: (data) => {
        setResetResult(data.generated_password ?? null);
        toast.success(`Password reset for ${user.email}`);
      },
    });
  }, [resetPassword]);

  const handleCopyResetPassword = useCallback(async () => {
    if (!resetResult) return;
    try {
      await copyToClipboard(resetResult);
      setResetCopied(true);
      toast.success("Password copied");
      setTimeout(() => setResetCopied(false), 2000);
    } catch {
      toast.error("Failed to copy password");
    }
  }, [resetResult]);

  const closeDialog = useCallback(() => {
    setShowCreate(false);
    setCreatedPassword(null);
    setName("");
    setEmail("");
    setUsername("");
    setRole("user");
  }, []);

  const userCount = (users ?? []).length;

  return (
    <>
      <PageHeader
        title="Users"
        breadcrumbs={[{ label: "Administration" }, { label: "Users" }]}
      />
      <div className="page-body mx-auto w-full">
        {isLoading ? (
          <TableSkeleton rows={5} cols={6} />
        ) : isError ? (
          <ErrorState message={error?.message} onRetry={() => refetch()} />
        ) : userCount === 0 ? (
          <EmptyState
            icon={Users}
            title="No users yet"
            description="Users will appear here once they sign up or are added by an admin."
            actionLabel="Add user"
            onAction={ssoOnly ? undefined : () => setShowCreate(true)}
          />
        ) : (
          <AdminPanel
            title="Organization users"
            subtitle={`${userCount} ${userCount === 1 ? "account" : "accounts"}`}
            action={
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" onClick={() => setShowBulkDept(true)}>
                  <Users className="h-3.5 w-3.5" />
                  Bulk departments
                </Button>
                {!ssoOnly && (
                  <Button size="sm" onClick={() => setShowCreate(true)}>
                    <Plus className="h-3.5 w-3.5" />
                    Add user
                  </Button>
                )}
              </div>
            }
          >
            <div className="hidden overflow-x-auto md:block">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>User</TableHead>
                    <TableHead>Username</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Department</TableHead>
                    <TableHead className="text-right">Joined</TableHead>
                    <TableHead className="w-[76px]"><span className="sr-only">Actions</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(users ?? []).map((u: AdminUser) => (
                    <TableRow key={u.id}>
                      <TableCell>
                        <div className="flex items-center gap-2.5">
                          <span aria-hidden="true" className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-raised text-3xs font-medium text-muted-foreground">
                            {initials(u.name, u.email)}
                          </span>
                          <span className="text-xs font-medium">{u.name ?? "—"}</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{u.username ? `@${u.username}` : "—"}</TableCell>
                      <TableCell className="font-mono text-2xs text-muted-foreground">{u.email ?? "—"}</TableCell>
                      <TableCell><RoleSelect userId={u.id} currentRole={u.role} /></TableCell>
                      <TableCell><DepartmentInput userId={u.id} currentDept={u.department} /></TableCell>
                      <TableCell className="text-right text-2xs tabular-nums text-muted-foreground">
                        {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-1">
                          {!ssoOnly && (
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-muted-foreground"
                              aria-label={`Reset password for ${u.name ?? u.email}`}
                              title="Reset password"
                              onClick={() => setResetTarget(u)}
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-muted-foreground hover:text-destructive"
                            aria-label={`Delete ${u.name ?? u.email}`}
                            title="Delete user"
                            onClick={() => setDeleteTarget(u)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <div className="divide-y divide-border md:hidden">
              {(users ?? []).map((u: AdminUser) => (
                <article key={u.id} className="p-4">
                  <div className="flex items-start gap-3">
                    <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface-raised text-2xs font-medium text-muted-foreground">
                      {initials(u.name, u.email)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2 className="truncate text-sm font-medium">{u.name ?? "Unnamed user"}</h2>
                      <p className="truncate font-mono text-2xs text-muted-foreground">{u.email ?? "No email"}</p>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      {!ssoOnly && (
                        <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`Reset password for ${u.name ?? u.email}`} onClick={() => setResetTarget(u)}>
                          <RotateCcw className="h-3.5 w-3.5" />
                        </Button>
                      )}
                      <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" aria-label={`Delete ${u.name ?? u.email}`} onClick={() => setDeleteTarget(u)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3">
                    <div><dt className="text-3xs uppercase tracking-wide text-muted-foreground">Role</dt><dd className="mt-1"><RoleSelect userId={u.id} currentRole={u.role} /></dd></div>
                    <div><dt className="text-3xs uppercase tracking-wide text-muted-foreground">Department</dt><dd className="mt-2"><DepartmentInput userId={u.id} currentDept={u.department} /></dd></div>
                  </dl>
                </article>
              ))}
            </div>
          </AdminPanel>
        )}
      </div>

      {/* Create User Dialog */}
      <Dialog open={showCreate} onOpenChange={(open) => { if (!open) closeDialog(); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{createdPassword ? "User Created" : "Add User"}</DialogTitle>
            <DialogDescription>
              {createdPassword
                ? "Save this password — it will not be shown again."
                : "Create a new user account. They will receive a password for authentication."}
            </DialogDescription>
          </DialogHeader>

          {createdPassword ? (
            <div className="space-y-4">
              <div className="rounded-md border border-border bg-muted/30 p-3">
                <div className="flex items-center gap-2 mb-2">
                  <Key className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Password</span>
                </div>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-[family-name:var(--font-mono)] text-foreground break-all flex-1 select-all">
                    {createdPassword}
                  </code>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0 shrink-0" onClick={handleCopyPassword}>
                    {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button variant="ghost" size="sm" onClick={closeDialog}>Done</Button>
                <Button size="sm" onClick={() => setCreatedPassword(null)}>Create Another</Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-xs font-medium text-muted-foreground">Name</label>
                <Input
                  placeholder="Jane Smith"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="h-8 text-sm"
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-medium text-muted-foreground">Email</label>
                <Input
                  type="email"
                  placeholder="jane@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-8 text-sm"
                  onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-medium text-muted-foreground">Username (optional)</label>
                <Input
                  placeholder="jane_smith"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="h-8 text-sm"
                  onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-medium text-muted-foreground">Role</label>
                <PickerSelect
                  value={role}
                  onValueChange={setRole}
                  inputClassName="h-8 text-sm"
                  options={assignableRoles.map((r) => ({ value: r, label: ROLE_LABELS[r] }))}
                />
              </div>
              <DialogFooter>
                <Button variant="ghost" size="sm" onClick={closeDialog}>Cancel</Button>
                <Button
                  size="sm"
                  onClick={handleCreate}
                  disabled={createUser.isPending || !name.trim() || !email.trim()}
                >
                  {createUser.isPending ? (
                    <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Creating...</>
                  ) : (
                    "Create User"
                  )}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Reset Password Dialog */}
      <Dialog open={!!resetTarget} onOpenChange={(open) => { if (!open) { setResetTarget(null); setResetResult(null); setResetCopied(false); } }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{resetResult ? "Password Reset" : "Reset Password"}</DialogTitle>
            <DialogDescription>
              {resetResult
                ? "Save this temporary password. The user will be required to change it on next login."
                : <>Generate a temporary password for <strong>{resetTarget?.name}</strong> ({resetTarget?.email}). They will be required to change it on next login.</>}
            </DialogDescription>
          </DialogHeader>

          {resetResult ? (
            <div className="space-y-4">
              <div className="rounded-md border border-border bg-muted/30 p-3">
                <div className="flex items-center gap-2 mb-2">
                  <Key className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Temporary Password</span>
                </div>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-[family-name:var(--font-mono)] text-foreground break-all flex-1 select-all">
                    {resetResult}
                  </code>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0 shrink-0" onClick={handleCopyResetPassword}>
                    {resetCopied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button size="sm" onClick={() => { setResetTarget(null); setResetResult(null); setResetCopied(false); }}>Done</Button>
              </DialogFooter>
            </div>
          ) : (
            <DialogFooter>
              <Button variant="ghost" size="sm" onClick={() => setResetTarget(null)}>Cancel</Button>
              <Button
                size="sm"
                onClick={() => { if (resetTarget) handleResetPassword(resetTarget); }}
                disabled={resetPassword.isPending}
              >
                {resetPassword.isPending ? (
                  <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Resetting...</>
                ) : (
                  "Reset Password"
                )}
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete User Confirmation Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete User</DialogTitle>
            <DialogDescription>
              This will permanently delete <strong>{deleteTarget?.name}</strong> ({deleteTarget?.email}) and all associated data.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                if (!deleteTarget) return;
                deleteUser.mutate(deleteTarget.id, {
                  onSuccess: () => setDeleteTarget(null),
                });
              }}
              disabled={deleteUser.isPending}
            >
              {deleteUser.isPending ? (
                <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Deleting...</>
              ) : (
                "Delete User"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk Department Import Dialog */}
      <Dialog open={showBulkDept} onOpenChange={(open) => { if (!open) { setShowBulkDept(false); setBulkCsv(""); } }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Bulk Import Departments</DialogTitle>
            <DialogDescription>
              Paste CSV data with one user per line: <code className="text-xs bg-muted px-1 rounded">email,department</code>
            </DialogDescription>
          </DialogHeader>
          <textarea
            value={bulkCsv}
            onChange={(e) => setBulkCsv(e.target.value)}
            placeholder={"alice@company.com,Engineering\nbob@company.com,Product\ncharlie@company.com,DevOps"}
            className="w-full h-40 rounded-md border border-input bg-transparent px-3 py-2 text-sm font-mono resize-y focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => { setShowBulkDept(false); setBulkCsv(""); }}>Cancel</Button>
            <Button
              size="sm"
              disabled={bulkLoading || !bulkCsv.trim()}
              onClick={async () => {
                setBulkLoading(true);
                try {
                  const entries = bulkCsv.trim().split("\n").map((line) => {
                    const [email, ...rest] = line.split(",");
                    return { email: email.trim(), department: rest.join(",").trim() };
                  }).filter((e) => e.email && e.department);
                  const result = await admin.bulkDepartment(entries);
                  toast.success(`Updated ${result.updated} users${result.not_found.length > 0 ? `, ${result.not_found.length} not found` : ""}`);
                  if (result.not_found.length > 0) {
                    toast.error(`Not found: ${result.not_found.slice(0, 5).join(", ")}${result.not_found.length > 5 ? "..." : ""}`);
                  }
                  setShowBulkDept(false);
                  setBulkCsv("");
                  refetch();
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Bulk import failed");
                } finally {
                  setBulkLoading(false);
                }
              }}
            >
              {bulkLoading ? (
                <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Importing...</>
              ) : (
                "Import"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
