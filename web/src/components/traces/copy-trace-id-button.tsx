// SPDX-FileCopyrightText: 2026 Sagar Kumar <sagar0206mahran@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { MouseEvent } from "react";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn, copyToClipboard } from "@/lib/utils";

/**
 * Icon button that copies a trace ID (the session ID) to the clipboard.
 * Trace list rows open the trace on click, so the click stops here instead of
 * reaching the row.
 */
export function CopyTraceIdButton({ traceId, className }: { traceId: string; className?: string }) {
  async function handleCopy(e: MouseEvent<HTMLButtonElement>) {
    e.stopPropagation();
    try {
      await copyToClipboard(traceId);
      toast.success("Trace ID copied");
    } catch {
      toast.error("Could not copy the trace ID");
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      onClick={handleCopy}
      aria-label="Copy trace ID"
      title="Copy trace ID"
      className={cn("h-5 w-5 shrink-0 text-muted-foreground [&_svg]:size-3", className)}
    >
      <Copy />
    </Button>
  );
}
