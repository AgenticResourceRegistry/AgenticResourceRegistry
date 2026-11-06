// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Loader2, MessageCircle, X } from "lucide-react";
import { toast } from "sonner";
import type { UsagePingContact, UsagePingTeamSize } from "@/lib/types";
import {
  useDeferUsagePingContact,
  useSaveUsagePingContact,
  useUsagePingContact,
} from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const TEAM_SIZES: { value: Exclude<UsagePingTeamSize, "">; label: string }[] = [
  { value: "1-10", label: "1 to 10 people" },
  { value: "11-50", label: "11 to 50 people" },
  { value: "51-200", label: "51 to 200 people" },
  { value: "201-1000", label: "201 to 1,000 people" },
  { value: "1000+", label: "More than 1,000 people" },
];

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Lets a super admin share a contact person with the Agentic Resource Registry team.
 * `mode="prompt"` is the first-run or banner ask and offers "Not now" / "Don't ask again";
 * `mode="edit"` is the settings page, where closing simply cancels.
 */
export function StayInTouchDialog({
  open,
  onOpenChange,
  initial,
  mode,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: UsagePingContact | null;
  mode: "prompt" | "edit";
}) {
  const ids = useId();
  const save = useSaveUsagePingContact();
  const defer = useDeferUsagePingContact();
  const [name, setName] = useState(initial?.name ?? "");
  const [email, setEmail] = useState(initial?.email ?? "");
  const [role, setRole] = useState(initial?.role ?? "");
  const [teamSize, setTeamSize] = useState<UsagePingTeamSize>(initial?.team_size ?? "");
  const [consent, setConsent] = useState(Boolean(initial));

  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setEmail(initial?.email ?? "");
    setRole(initial?.role ?? "");
    setTeamSize(initial?.team_size ?? "");
    setConsent(Boolean(initial));
  }, [open, initial]);

  const emailValid = EMAIL_PATTERN.test(email.trim());
  const canSubmit = name.trim().length > 0 && emailValid && consent && !save.isPending;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    try {
      await save.mutateAsync({ name: name.trim(), email: email.trim(), role: role.trim(), team_size: teamSize, consent: true });
      toast.success("Thanks. The team will reach out to this contact.");
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save the contact");
    }
  }

  // A ref, not defer.isPending: repeated Escape presses arrive before the next render.
  const deferring = useRef(false);

  async function postpone(never: boolean) {
    if (deferring.current) return;
    deferring.current = true;
    try {
      await defer.mutateAsync(never);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save your choice");
    } finally {
      deferring.current = false;
      onOpenChange(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Dismissing the first-run ask counts as "Not now", so it can return later as a banner.
        if (!next && mode === "prompt" && !save.isSuccess) void postpone(false);
        else onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-[30rem]">
        <form onSubmit={submit} className="space-y-5">
          <DialogHeader>
            <DialogTitle>Stay in touch with the team</DialogTitle>
            <DialogDescription>
              Share a contact and the Agentic Resource Registry maintainers can help with rollout, answer questions, and
              hear what your team needs next. Nothing is shared unless you agree below.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor={`${ids}-name`}>Your name</Label>
              <Input id={`${ids}-name`} value={name} onChange={(event) => setName(event.target.value)} maxLength={120} autoComplete="name" required />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${ids}-email`}>Work email</Label>
              <Input
                id={`${ids}-email`}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                aria-invalid={email.length > 0 && !emailValid}
                required
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={`${ids}-role`}>Role <span className="font-normal text-muted-foreground">(optional)</span></Label>
                <Input id={`${ids}-role`} value={role} onChange={(event) => setRole(event.target.value)} maxLength={80} placeholder="Platform lead" />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`${ids}-team`}>Team size <span className="font-normal text-muted-foreground">(optional)</span></Label>
                <Select value={teamSize || undefined} onValueChange={(value) => setTeamSize(value as UsagePingTeamSize)}>
                  <SelectTrigger id={`${ids}-team`}>
                    <SelectValue placeholder="Choose" />
                  </SelectTrigger>
                  <SelectContent>
                    {TEAM_SIZES.map((option) => (
                      <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-start gap-2.5 rounded-lg bg-surface-sunken/70 p-3">
              <Checkbox id={`${ids}-consent`} checked={consent} onCheckedChange={(value) => setConsent(value === true)} className="mt-0.5" />
              <Label htmlFor={`${ids}-consent`} className="text-xs font-normal leading-5 text-muted-foreground">
                The maintainers may contact me about this deployment. This contact is sent with usage reports until I
                remove it under Admin, Settings, Usage reporting.
              </Label>
            </div>
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            {mode === "prompt" ? (
              <div className="flex gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={() => void postpone(true)} disabled={defer.isPending}>
                  Don't ask again
                </Button>
                <Button type="button" variant="outline" size="sm" onClick={() => void postpone(false)} disabled={defer.isPending}>
                  Not now
                </Button>
              </div>
            ) : (
              <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
            )}
            <Button type="submit" size="sm" disabled={!canSubmit}>
              {save.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Share contact
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** First-run dialog and growth banner for super admins, driven by the server's prompt state. */
export function StayInTouchPrompt() {
  const { data } = useUsagePingContact();
  const defer = useDeferUsagePingContact();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (data?.prompt === "dialog") setOpen(true);
  }, [data?.prompt]);

  function dismiss(never: boolean) {
    defer.mutate(never, {
      onError: (error) => toast.error(error instanceof Error ? error.message : "Could not save your choice"),
    });
  }

  if (!data?.prompt) return null;

  return (
    <>
      {data.prompt === "banner" ? (
        <div role="region" aria-label="Stay in touch" className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface-raised px-4 py-2.5 text-sm">
          <MessageCircle className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <p className="min-w-0 flex-1 text-muted-foreground">
            <span className="font-medium text-foreground">Your team is growing here.</span> Want a direct line to the
            maintainers for rollout help and roadmap input?
          </p>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => setOpen(true)}>Get in touch</Button>
            <Button size="sm" variant="ghost" onClick={() => dismiss(false)} disabled={defer.isPending}>Not now</Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              aria-label="Don't ask again"
              title="Don't ask again"
              onClick={() => dismiss(true)}
              disabled={defer.isPending}
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          </div>
        </div>
      ) : null}
      <StayInTouchDialog open={open} onOpenChange={setOpen} initial={data.contact} mode="prompt" />
    </>
  );
}
