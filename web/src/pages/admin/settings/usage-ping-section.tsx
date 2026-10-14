// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react";
import { Activity, CheckCircle2, Eye, Loader2, Send, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { admin } from "@/lib/api";
import type { AdminSetting, UsagePingFrequency } from "@/lib/types";
import { useSendUsagePing, useUsagePingPreview, useUsagePingStatus } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const FREQUENCY_OPTIONS: { value: UsagePingFrequency; label: string; detail: string }[] = [
  { value: "every_6_hours", label: "Every 6 hours", detail: "00:30, 06:30, 12:30, and 18:30 UTC" },
  { value: "daily", label: "Daily", detail: "06:30 UTC each day" },
  { value: "weekly", label: "Weekly", detail: "Monday at 06:30 UTC" },
];

function valueOf(settings: AdminSetting[], key: string, fallback = "") {
  return settings.find((setting) => setting.key === key)?.value ?? fallback;
}

// The settings list only returns stored rows, so an unset key must fall back to
// the server default (usage_ping.enabled = "true") rather than reading as off.
function enabledOf(settings: AdminSetting[]): boolean {
  return valueOf(settings, "usage_ping.enabled", "true") === "true";
}

function frequencyOf(settings: AdminSetting[]): UsagePingFrequency {
  const value = valueOf(settings, "usage_ping.frequency", "every_6_hours");
  return FREQUENCY_OPTIONS.find((option) => option.value === value)?.value ?? "every_6_hours";
}

export function UsagePingSection({ settings, onChanged }: { settings: AdminSetting[]; onChanged: () => void }) {
  const { data: status, refetch } = useUsagePingStatus();
  const preview = useUsagePingPreview();
  const sender = useSendUsagePing();
  const [companyName, setCompanyName] = useState(() => valueOf(settings, "usage_ping.company_name"));
  const [frequency, setFrequency] = useState<UsagePingFrequency>(() => frequencyOf(settings));
  const [enabled, setEnabled] = useState(() => enabledOf(settings));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setCompanyName(valueOf(settings, "usage_ping.company_name"));
    setFrequency(frequencyOf(settings));
    setEnabled(enabledOf(settings));
  }, [settings]);

  async function save() {
    setSaving(true);
    try {
      await admin.updateSetting("usage_ping.company_name", { value: companyName.trim() });
      await admin.updateSetting("usage_ping.frequency", { value: frequency });
      await admin.updateSetting("usage_ping.enabled", { value: enabled ? "true" : "false" });
      await refetch();
      onChanged();
      const frequencyLabel = FREQUENCY_OPTIONS.find((option) => option.value === frequency)?.label.toLowerCase();
      toast.success(enabled ? `${frequencyLabel} usage reporting enabled` : "Usage reporting disabled");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save usage reporting settings");
    } finally {
      setSaving(false);
    }
  }

  async function sendNow() {
    try {
      await sender.mutateAsync();
      toast.success("Usage report accepted by usage.observal.io");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not send usage report");
    }
  }

  return (
    <section className="animate-in">
      <h3 className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        <Activity className="h-3.5 w-3.5" /> Usage reporting
      </h3>
      <div className="rounded-md border border-border bg-card px-4 py-4 space-y-4">
        <div className="flex items-start justify-between gap-6">
          <div className="max-w-2xl">
            <p className="text-sm font-medium">Share aggregate product usage with Observal</p>
            <p className="mt-1 text-xs font-medium text-foreground">Enabled by default · Every 6 hours</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              Sends aggregate reports to usage.observal.io on the schedule you choose. Reports include company
              and instance identity, version, aggregate counts, feature flags, and harness totals. Prompts, traces,
              source code, user identities, and credentials are never included.
            </p>
          </div>
          <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Enable usage reporting" />
        </div>

        <div className="grid gap-3 border-t border-border pt-4 md:grid-cols-[minmax(0,1fr)_minmax(12rem,0.5fr)_auto] md:items-start">
          <div className="space-y-1.5">
            <label htmlFor="usage-reporting-company" className="block text-xs font-medium leading-4">Company name</label>
            <Input
              id="usage-reporting-company"
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              maxLength={160}
              placeholder="Enter your company name"
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="usage-reporting-frequency" className="block text-xs font-medium leading-4">Reporting frequency</label>
            <Select value={frequency} onValueChange={(value) => setFrequency(value as UsagePingFrequency)}>
              <SelectTrigger id="usage-reporting-frequency" className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FREQUENCY_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              {FREQUENCY_OPTIONS.find((option) => option.value === frequency)?.detail}
            </p>
          </div>
          <Button className="md:mt-[22px]" onClick={save} disabled={saving}>
            {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-1.5 h-4 w-4" />}
            Save consent
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border pt-4 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5"><CheckCircle2 className="h-3.5 w-3.5" /> Destination: {status?.collector_url ?? "usage.observal.io"}</span>
          <span>Last sent: {status?.last_success_at ? new Date(status.last_success_at).toLocaleString() : "Never"}</span>
          <span>Next run: {status?.next_scheduled_at ? new Date(status.next_scheduled_at).toLocaleString() : "Loading"}</span>
        </div>
        {status?.last_error ? <p className="text-xs text-destructive">Last delivery failed: {status.last_error}</p> : null}
        {enabled && !companyName.trim() ? <p className="text-xs text-muted-foreground">Reports identify this instance as unknown until you add a company name.</p> : null}

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => preview.mutate()} disabled={preview.isPending}>
            <Eye className="mr-1.5 h-3.5 w-3.5" /> Preview exact payload
          </Button>
          <Button variant="outline" size="sm" onClick={sendNow} disabled={!status?.enabled || sender.isPending}>
            {sender.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}
            Send now
          </Button>
        </div>

        {preview.data?.payload ? (
          <div className="rounded-md bg-muted/60 p-3">
            <p className="mb-2 text-xs font-medium">Exact payload</p>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-5 text-muted-foreground">{JSON.stringify(preview.data.payload, null, 2)}</pre>
          </div>
        ) : null}
      </div>
    </section>
  );
}
