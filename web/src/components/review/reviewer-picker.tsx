// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useId, useRef, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { useEligibleReviewers } from "@/hooks/use-api";
import type { EligibleReviewer } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export function ReviewerPicker({ reviewNumber, busy, onRequest }: {
  reviewNumber: string;
  busy: boolean;
  onRequest: (id: string, onSuccess: () => void) => void;
}) {
  const listId = useId();
  const inputAnchor = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<EligibleReviewer | null>(null);
  const [active, setActive] = useState(-1);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(text.trim()), 180);
    return () => clearTimeout(timer);
  }, [text]);
  const { data, isPending, isError, refetch } = useEligibleReviewers(reviewNumber, query, open);
  // A stale result must never be selectable after the input changes.
  const results = query === text.trim() ? (data ?? []) : [];
  const choose = (person: EligibleReviewer) => {
    setSelected(person);
    setText(person.name);
    setOpen(false);
    setActive(-1);
  };
  return <div className="mt-3 flex flex-wrap gap-2">
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div ref={inputAnchor} className="relative min-w-40 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Find a reviewer"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={open}
            aria-controls={listId}
            aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
            placeholder="Find a reviewer"
            value={text}
            disabled={busy}
            className="h-8 w-full pl-8"
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onChange={event => { setText(event.target.value); setSelected(null); setActive(-1); setOpen(true); }}
            onKeyDown={event => {
              if (event.key === "Escape") setOpen(false);
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setOpen(true);
                setActive(index => {
                  if (!results.length) return -1;
                  if (index < 0 || index >= results.length) return event.key === "ArrowDown" ? 0 : results.length - 1;
                  return (index + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
                });
              }
              if (event.key === "Enter" && open && results.length) {
                event.preventDefault();
                // Results can shrink after a refetch; never choose an index that no longer exists.
                choose(results[active >= 0 && active < results.length ? active : 0]);
              }
            }}
          />
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] min-w-48 p-1"
        onOpenAutoFocus={event => event.preventDefault()}
        // Focusing the input opens the popover during the same pointer event.
        // Radix otherwise treats that first click on the anchor as an outside click and closes it.
        onInteractOutside={event => {
          if (inputAnchor.current?.contains(event.target as Node)) event.preventDefault();
        }}
      >
        <div id={listId} role="listbox" aria-label="Eligible reviewers" className="max-h-56 overflow-y-auto">
          {/* Background refetches keep the current list; only a new search with no data yet shows loading. */}
          {isPending || query !== text.trim() ? <div role="status" className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Loading reviewers...</div>
            : isError ? <div role="status" className="px-2 py-2 text-xs">Could not load reviewers. <button type="button" className="underline" onClick={() => refetch()}>Try again</button></div>
            : results.length ? results.map((person, index) => <button
              key={person.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={active === index}
              type="button"
              // Focus stays on the combobox; options are reached with arrow keys via aria-activedescendant.
              tabIndex={-1}
              onMouseDown={event => event.preventDefault()}
              onClick={() => choose(person)}
              // Pointer and keyboard share one highlight, as in GitHub's reviewer menu.
              onMouseMove={() => setActive(index)}
              ref={active === index ? node => node?.scrollIntoView({ block: "nearest" }) : undefined}
              className={cn(
                "flex w-full items-center gap-2 rounded px-2 py-2 text-left text-xs focus-visible:outline-2 focus-visible:outline-ring",
                active === index && "bg-primary text-primary-foreground",
              )}
            ><span className="min-w-0 flex-1 truncate font-medium">{person.name} <span className={cn("font-normal", active === index ? "text-primary-foreground/80" : "text-muted-foreground")}>@{person.username}</span></span><span className={cn("shrink-0", active === index ? "text-primary-foreground/80" : "text-muted-foreground")}>{person.role.replace("_", " ")}</span></button>)
              : <div role="status" className="px-2 py-2 text-xs text-muted-foreground">No eligible reviewers match. Try another name.</div>}
        </div>
      </PopoverContent>
    </Popover>
    <Button size="sm" variant="outline" disabled={!selected || busy} onClick={() => {
      if (selected) onRequest(selected.id, () => { setText(""); setQuery(""); setSelected(null); });
    }}>Request reviewer</Button>
  </div>;
}
