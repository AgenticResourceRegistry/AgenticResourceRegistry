// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { PublicReviewPolicy } from "@/lib/types";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

export const policyKinds = ["agent", "mcp", "skill", "hook", "prompt", "sandbox"];

export const defaultPolicy: PublicReviewPolicy = {
  required_approvals: {},
  self_approval: "not_counted",
  dismiss_stale_approvals: true,
  require_resolved_threads: false,
  auto_publish: false,
};

type FlagName = "dismiss_stale_approvals" | "require_resolved_threads" | "auto_publish";

/** One policy block. Zero required approvals plus automatic publishing means "auto approve". */
export function PolicyFields({ value, onChange, selfApprovalHint }: {
  value: PublicReviewPolicy;
  onChange: (next: PublicReviewPolicy) => void;
  selfApprovalHint?: string;
}) {
  const flag = (name: FlagName, label: string, description: string) => (
    <label className="flex items-center justify-between gap-4 border-b border-border py-4">
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="block text-xs text-muted-foreground">{description}</span>
      </span>
      <Switch checked={value[name]} onCheckedChange={checked => onChange({ ...value, [name]: checked })} />
    </label>
  );
  return (
    <div>
      <h3 className="text-sm font-semibold">Required approvals by type</h3>
      <p className="mt-1 text-xs text-muted-foreground">Use 0 to skip review for a type. Pair it with automatic publishing to auto approve.</p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {policyKinds.map(kind => (
          <label key={kind} className="text-sm capitalize">{kind}
            <Input type="number" min={0} max={5} className="mt-1" value={value.required_approvals[kind] ?? 1}
              onChange={event => onChange({ ...value, required_approvals: { ...value.required_approvals, [kind]: Number(event.target.value) } })} />
          </label>
        ))}
      </div>
      <div className="mt-6 border-t border-border">
        {flag("dismiss_stale_approvals", "Dismiss stale approvals", "Approvals on earlier revisions stop counting.")}
        {flag("require_resolved_threads", "Require resolved conversations", "Unresolved threads block publication.")}
        {flag("auto_publish", "Publish automatically", "Publish as soon as every requirement is met (auto approve when approvals are 0).")}
      </div>
      <label className="mt-4 block text-sm font-medium">Self-approval
        <select value={value.self_approval} onChange={event => onChange({ ...value, self_approval: event.target.value as PublicReviewPolicy["self_approval"] })} className="mt-1 block w-full rounded-md border border-border bg-background p-2">
          <option value="not_counted">Recorded, not counted</option>
          <option value="counted">Authors may approve and publish their own work</option>
        </select>
        {selfApprovalHint && <span className="mt-1 block text-xs font-normal text-muted-foreground">{selfApprovalHint}</span>}
      </label>
    </div>
  );
}
