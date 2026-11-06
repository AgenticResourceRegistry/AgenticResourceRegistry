// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Eye, EyeOff, Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { admin } from "@/lib/api";
import type { AdminSetting, UsagePingFrequency } from "@/lib/types";
import {
  useSendUsagePing,
  useUsagePingContact,
  useUsagePingPreview,
  useUsagePingStatus,
  useWithdrawUsagePingContact,
} from "@/hooks/use-api";
import { StayInTouchDialog } from "@/components/usage/stay-in-touch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CardFooter, SettingRow, SettingsCard, SettingsGroup, SwitchStateLabel } from "./settings-ui";
import { rowDomId } from "./settings-helpers";

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

// Unset means the server default: super administrator email addresses are included.
function includeEmailsOf(settings: AdminSetting[]): boolean {
  return valueOf(settings, "usage_ping.include_admin_emails", "true") === "true";
}

function frequencyOf(settings: AdminSetting[]): UsagePingFrequency {
  const value = valueOf(settings, "usage_ping.frequency", "every_6_hours");
  return FREQUENCY_OPTIONS.find((option) => option.value === value)?.value ?? "every_6_hours";
}

export function UsagePingSection({
  settings,
  onChanged,
  onDirtyChange,
}: {
  settings: AdminSetting[];
  onChanged: () => void;
  onDirtyChange?: (sectionId: string, dirty: boolean) => void;
}) {
  const { data: status, refetch } = useUsagePingStatus();
  const preview = useUsagePingPreview();
  const sender = useSendUsagePing();
  const [companyName, setCompanyName] = useState(() => valueOf(settings, "usage_ping.company_name"));
  const [frequency, setFrequency] = useState<UsagePingFrequency>(() => frequencyOf(settings));
  const [enabled, setEnabled] = useState(() => enabledOf(settings));
  const [includeEmails, setIncludeEmails] = useState(() => includeEmailsOf(settings));
  const [saving, setSaving] = useState(false);
  const [showPayload, setShowPayload] = useState(false);
  const contact = useUsagePingContact();
  const withdraw = useWithdrawUsagePingContact();
  const [contactOpen, setContactOpen] = useState(false);

  // Follow the server only for fields the user hasn't edited, so saving another
  // card (which refetches settings) never discards a draft here.
  const lastStored = useRef({
    companyName: valueOf(settings, "usage_ping.company_name"),
    frequency: frequencyOf(settings),
    enabled: enabledOf(settings),
    includeEmails: includeEmailsOf(settings),
  });
  useEffect(() => {
    const previous = lastStored.current;
    const next = {
      companyName: valueOf(settings, "usage_ping.company_name"),
      frequency: frequencyOf(settings),
      enabled: enabledOf(settings),
      includeEmails: includeEmailsOf(settings),
    };
    setCompanyName((current) => (current === previous.companyName ? next.companyName : current));
    setFrequency((current) => (current === previous.frequency ? next.frequency : current));
    setEnabled((current) => (current === previous.enabled ? next.enabled : current));
    setIncludeEmails((current) => (current === previous.includeEmails ? next.includeEmails : current));
    lastStored.current = next;
  }, [settings]);

  const dirtyCount = [
    companyName !== valueOf(settings, "usage_ping.company_name"),
    frequency !== frequencyOf(settings),
    enabled !== enabledOf(settings),
    includeEmails !== includeEmailsOf(settings),
  ].filter(Boolean).length;
  useEffect(() => {
    onDirtyChange?.("usage", dirtyCount > 0);
  }, [dirtyCount, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.("usage", false), [onDirtyChange]);

  function discard() {
    setCompanyName(valueOf(settings, "usage_ping.company_name"));
    setFrequency(frequencyOf(settings));
    setEnabled(enabledOf(settings));
    setIncludeEmails(includeEmailsOf(settings));
  }

  async function save() {
    setSaving(true);
    try {
      const trimmed = companyName.trim();
      await admin.updateSetting("usage_ping.company_name", { value: trimmed });
      await admin.updateSetting("usage_ping.frequency", { value: frequency });
      await admin.updateSetting("usage_ping.enabled", { value: enabled ? "true" : "false" });
      await admin.updateSetting("usage_ping.include_admin_emails", { value: includeEmails ? "true" : "false" });
      // Show what was stored, so the card isn't left looking unsaved after a trim.
      setCompanyName(trimmed);
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

  async function removeContact() {
    try {
      await withdraw.mutateAsync(undefined);
      toast.success("Contact removed from future usage reports");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not remove the contact");
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

  const enabledId = rowDomId("usage_ping.enabled") + "-control";
  const companyId = rowDomId("usage_ping.company_name") + "-control";
  const includeEmailsId = rowDomId("usage_ping.include_admin_emails") + "-control";
  const frequencyId = rowDomId("usage_ping.frequency") + "-control";
  return (
    <SettingsGroup id="usage" title="Usage reporting">
      <SettingsCard
        footer={
          <CardFooter
            persistent
            allowClean
            dirtyCount={dirtyCount}
            saving={saving}
            saveLabel="Save consent"
            onSave={() => void save()}
            onDiscard={discard}
          />
        }
      >
        <SettingRow
          settingKey="usage_ping.enabled"
          label="Share aggregate product usage with Agentic Resource Registry"
          htmlFor={enabledId}
          layout="inline"
          meta="Enabled by default · Every 6 hours"
          description="Sends reports to usage.observal.io on the schedule you choose. Reports include company and instance identity, the domains users reach this server on, user counts per email domain, super administrator email addresses (unless turned off below), sign-in provider domains, version, aggregate counts, feature flags, and harness totals. Prompts, traces, source code, and credentials are never included, and a contact person is sent only if you add one below."
        >
          <SwitchStateLabel checked={enabled} />
          <Switch id={enabledId} checked={enabled} onCheckedChange={setEnabled} aria-label="Enable usage reporting" />
        </SettingRow>
        <SettingRow
          settingKey="usage_ping.include_admin_emails"
          label="Include administrator email addresses"
          htmlFor={includeEmailsId}
          layout="inline"
          meta="On by default"
          description={
            status?.admin_emails.length
              ? `${includeEmails ? "Sent with each report" : "Would be sent if turned on"}: ${status.admin_emails.join(", ")}`
              : "Sends the email addresses of super administrators (up to 5) with each report."
          }
        >
          <SwitchStateLabel checked={includeEmails} />
          <Switch
            id={includeEmailsId}
            checked={includeEmails}
            onCheckedChange={setIncludeEmails}
            disabled={!enabled}
            aria-label="Include administrator email addresses in usage reports"
          />
        </SettingRow>
        <SettingRow settingKey="usage_ping.company_name" label="Company name" htmlFor={companyId}>
          <Input
            id={companyId}
            value={companyName}
            onChange={(event) => setCompanyName(event.target.value)}
            maxLength={160}
            placeholder="Enter your company name"
            className="min-h-9 flex-1 py-2"
          />
        </SettingRow>
        <SettingRow
          settingKey="usage_ping.frequency"
          label="Reporting frequency"
          htmlFor={frequencyId}
          description={FREQUENCY_OPTIONS.find((option) => option.value === frequency)?.detail}
        >
          <Select value={frequency} onValueChange={(value) => setFrequency(value as UsagePingFrequency)}>
            <SelectTrigger id={frequencyId} className="min-h-9 flex-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FREQUENCY_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          settingKey="usage_ping.contact"
          label="Contact person"
          description={
            contact.isError
              ? "Could not load the contact person. Reload the page to try again."
              : contact.data?.contact
                ? `Shared since ${new Date(contact.data.contact.consented_at).toLocaleDateString()}. Removing it stops sending the contact with future reports.`
                : "Optional. Add someone the maintainers can reach about this deployment."
          }
        >
          <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
            {contact.data?.contact ? (
              <span className="mr-auto min-w-0 truncate text-sm">
                {contact.data.contact.name} <span className="text-muted-foreground">· {contact.data.contact.email}</span>
              </span>
            ) : null}
            <Button variant="outline" size="sm" onClick={() => setContactOpen(true)} disabled={!contact.data}>
              {contact.data?.contact ? "Edit contact" : "Add contact"}
            </Button>
            {contact.data?.contact ? (
              <Button variant="ghost" size="sm" onClick={() => void removeContact()} disabled={withdraw.isPending}>
                Remove
              </Button>
            ) : null}
          </div>
        </SettingRow>
        <StayInTouchDialog open={contactOpen} onOpenChange={setContactOpen} initial={contact.data?.contact ?? null} mode="edit" />
        <div className="space-y-3 px-4 py-3.5 sm:px-[1.125rem]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5"><CheckCircle2 className="size-3.5" aria-hidden="true" /> Destination: <b className="font-medium text-foreground">{status?.collector_url ?? "usage.observal.io"}</b></span>
              <span>Last sent: <b className="font-medium text-foreground">{status?.last_success_at ? new Date(status.last_success_at).toLocaleString() : "Never"}</b></span>
              <span>Next run: <b className="font-medium tabular-nums text-foreground">{status?.next_scheduled_at ? new Date(status.next_scheduled_at).toLocaleString() : "Loading"}</b></span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                aria-expanded={showPayload}
                onClick={() => {
                  // Fetch fresh on every open: "exact payload" must reflect the latest saved values.
                  if (!showPayload) preview.mutate();
                  setShowPayload((open) => !open);
                }}
                disabled={preview.isPending}
              >
                {preview.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : showPayload ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                Preview exact payload
              </Button>
              <Button variant="outline" size="sm" onClick={sendNow} disabled={!status?.enabled || !status.configured || sender.isPending}>
                {sender.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Send aria-hidden="true" />}
                Send now
              </Button>
            </div>
          </div>
          {status?.last_error ? <p className="text-xs text-destructive">Last delivery failed: {status.last_error}</p> : null}
          {enabled && status && !status.configured ? <p className="text-xs text-warning">Set both the company name and Deployment Public URL before sending.</p> : null}
          {showPayload && !preview.isPending && preview.data?.payload ? (
            <div className="space-y-2">
              <p className="text-xs font-medium">Exact payload</p>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-surface-sunken/70 p-3 font-mono text-[11px] leading-5 text-muted-foreground">{JSON.stringify(preview.data.payload, null, 2)}</pre>
            </div>
          ) : null}
        </div>
      </SettingsCard>
    </SettingsGroup>
  );
}
