// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-FileCopyrightText: 2026 SrihariLegend <sriharilegend23@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useCallback, useEffect, useRef, useState } from "react";
import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  auth,
  admin,
  config,
  telemetry,
  getUserRole,
} from "@/lib/api";
import { hasMinRole } from "@/hooks/use-role-guard";

// ── Auth ────────────────────────────────────────────────────────────

export function useWhoami(
  enabled = typeof window !== "undefined" && !!sessionStorage.getItem("observal_access_token"),
) {
  return useQuery({
    queryKey: ["auth", "whoami"],
    queryFn: auth.whoami,
    enabled,
    retry: false,
  });
}

// ── Admin ───────────────────────────────────────────────────────────

export function useAdminUsers() {
  return useQuery({ queryKey: ["admin", "users"], queryFn: admin.users });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: admin.createUser,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("User created");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to create user");
    },
  });
}

export function useUpdateUserRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; role: string }) =>
      admin.updateRole(vars.id, { role: vars.role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("Role updated");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to update role");
    },
  });
}

export function useUpdateUserDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; department: string | null }) =>
      admin.updateDepartment(vars.id, { department: vars.department }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("Department updated");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to update department");
    },
  });
}

export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => admin.deleteUser(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("User deleted");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete user");
    },
  });
}

export function useResetPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => admin.resetPassword(id, { generate: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to reset password");
    },
  });
}

export function useAdminSettings() {
  return useQuery({ queryKey: ["admin", "settings"], queryFn: admin.settings });
}

export function useAdminSettingsSchema() {
  return useQuery({ queryKey: ["admin", "settings", "schema"], queryFn: admin.settingsSchema });
}

export function useUsagePingStatus() {
  return useQuery({
    queryKey: ["admin", "usage-ping"],
    queryFn: admin.usagePingStatus,
  });
}

export function useUsagePingPreview() {
  return useMutation({ mutationFn: admin.usagePingPreview });
}

export function useSendUsagePing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: admin.sendUsagePing,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "usage-ping"] }),
  });
}

export function useRestartStatus() {
  return useQuery({
    queryKey: ["admin", "restart-status"],
    queryFn: admin.restartStatus,
    refetchInterval: 30_000,
  });
}

const RESTART_POLL_TIMEOUT_MS = 120_000;
const RESTART_POLL_INTERVAL_MS = 2_000;
const RESTART_POLL_INITIAL_DELAY_MS = 3_000;

export function useRestartApi(onRestarted?: () => void) {
  const [restarting, setRestarting] = useState(false);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const restartApi = useCallback(async () => {
    if (!confirm("Restart the API? In-flight requests will be interrupted while the process restarts.")) return;
    setRestarting(true);
    try {
      await admin.restartApi();
      toast.info("API restart initiated. Waiting for it to come back.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to trigger API restart");
      setRestarting(false);
      return;
    }

    const deadline = Date.now() + RESTART_POLL_TIMEOUT_MS;
    const poll = async () => {
      if (cancelledRef.current) return;
      if (Date.now() > deadline) {
        toast.error("API did not return within 2 minutes. Check the container logs.");
        setRestarting(false);
        return;
      }
      try {
        await config.version();
        if (cancelledRef.current) return;
        toast.success("API is back up");
        setRestarting(false);
        onRestarted?.();
      } catch {
        window.setTimeout(poll, RESTART_POLL_INTERVAL_MS);
      }
    };
    window.setTimeout(poll, RESTART_POLL_INITIAL_DELAY_MS);
  }, [onRestarted]);

  return { restarting, restartApi };
}

// ── Audit & Security ────────────────────────────────────────────────

export function useAuditLog(filters?: Record<string, string>) {
  return useQuery({
    queryKey: ["admin", "audit-log", filters],
    queryFn: () => admin.auditLog(filters),
  });
}

export function useSecurityEvents(filters?: Record<string, string>) {
  return useQuery({
    queryKey: ["admin", "security-events", filters],
    queryFn: () => admin.securityEvents(filters),
  });
}

export function useSystemWarnings() {
  return useQuery({
    queryKey: ["admin", "system-warnings"],
    queryFn: admin.systemWarnings,
    refetchInterval: 60_000,
  });
}

// ── Retention ────────────────────────────────────────────────────────

export function useRetentionStats() {
  const role = getUserRole();
  return useQuery({
    queryKey: ["admin", "retention", "stats"],
    queryFn: admin.getRetentionStats,
    enabled: hasMinRole(role, "admin"),
  });
}

export function useRetentionWarnings() {
  const role = getUserRole();
  return useQuery({
    queryKey: ["admin", "retention", "warnings"],
    queryFn: admin.getRetentionWarnings,
    enabled: hasMinRole(role, "admin"),
  });
}

// ── Telemetry ───────────────────────────────────────────────────────

export function useTelemetryStatus() {
  return useQuery({
    queryKey: ["telemetry", "status"],
    queryFn: telemetry.status,
  });
}

// ── Migration ────────────────────────────────────────────────────────

export function useStartMigrationExport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope: string) => admin.migrateExport(scope),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "migration", "jobs"] });
      toast.success("Export job started");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to start export");
    },
  });
}

export function useStartMigrationImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (formData: FormData) => admin.migrateImport(formData),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "migration", "jobs"] });
      toast.success("Import job started");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to start import");
    },
  });
}

export function useStartMigrationValidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (formData: FormData) => admin.migrateValidate(formData),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "migration", "jobs"] });
      toast.success("Validation job started");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to start validation");
    },
  });
}

export function useMigrationJob(id: string | null) {
  return useQuery({
    queryKey: ["admin", "migration", "job", id],
    queryFn: () => admin.migrateJob(id!),
    enabled: !!id,
    refetchInterval: 1500,
  });
}

export function useMigrationJobs() {
  return useQuery({
    queryKey: ["admin", "migration", "jobs"],
    queryFn: admin.migrateJobs,
  });
}

export function useMigrationDownloadToken() {
  return useMutation({
    mutationFn: (vars: { jobId: string; name: string }) =>
      admin.migrateDownloadToken(vars.jobId, vars.name),
    onError: (err: Error) => {
      toast.error(err.message || "Failed to get download token");
    },
  });
}
