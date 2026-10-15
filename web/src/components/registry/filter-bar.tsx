// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/**
 * GitHub-style search bar: free text plus `qualifier:value` filters that turn
 * into removable chips. Focusing the bar lists the available qualifiers;
 * typing `team:` lists that qualifier's values.
 */

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Search, X } from "lucide-react";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { useUserSearch } from "@/hooks/use-user-search";
import { cn } from "@/lib/utils";

export type FilterOption = { value: string; label: string; hint?: string };

export type FilterField = {
  /** Key in the values map (and URL). */
  key: string;
  /** Chip label; its lower-cased, dashed form is the typed qualifier (`Task type` -> `task-type:`). */
  label: string;
  /** One-line description shown in the qualifier list. */
  hint: string;
  options?: FilterOption[];
  /** Value is a username, suggested from user search; any typed name can be committed with Enter. */
  user?: boolean;
  /** Shown as a fixed chip until another value is chosen. */
  defaultValue?: string;
};

const qualifier = (f: FilterField) => f.label.toLowerCase().replace(/\s+/g, "-");
// The last whitespace-separated word, when it looks like `qualifier:partial-value`.
const TOKEN = /(?:^|\s)([\w-]+):(\S*)$/;

type Item =
  | { kind: "key"; field: FilterField }
  | { kind: "value"; field: FilterField; value: string; label: string; hint?: string };

export function FilterBar({
  fields,
  values,
  onValueChange,
  text,
  onTextChange,
  onClear,
  placeholder,
}: {
  fields: FilterField[];
  values: Record<string, string | undefined>;
  onValueChange: (key: string, value: string | undefined) => void;
  /** Free-text part of the search. */
  text: string;
  onTextChange: (text: string) => void;
  onClear: () => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState(text);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const barRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const token = draft.match(TOKEN);
  const tokenField = token ? fields.find((f) => qualifier(f) === token[1].toLowerCase()) : undefined;
  const base = token && tokenField ? draft.slice(0, token.index).trim() : draft;

  // Follow `text` when it changes from outside (URL back/forward, Clear all). Typing keeps
  // `text` equal to the free-text part of the draft, so this does nothing while typing.
  const baseRef = useRef(base);
  baseRef.current = base;
  useEffect(() => {
    if (text !== baseRef.current.trim()) setDraft(text);
  }, [text]);
  const query = tokenField ? token![2].toLowerCase() : "";
  const { data: users = [] } = useUserSearch(query, { enabled: open && !!tokenField?.user, limit: 6 });

  const chips = fields
    .map((f) => ({ f, value: values[f.key] ?? f.defaultValue }))
    .filter((c): c is { f: FilterField; value: string } => !!c.value);
  const removable = chips.filter((c) => c.value !== c.f.defaultValue);

  const word = draft.split(/\s+/).pop()!.toLowerCase();
  let items: Item[];
  if (tokenField?.user) {
    items = users
      .filter((u) => u.username)
      .map((u) => ({ kind: "value", field: tokenField, value: u.username!, label: u.name, hint: `@${u.username}` }));
  } else if (tokenField) {
    items = (tokenField.options ?? [])
      .filter((o) => `${o.label} ${o.value}`.toLowerCase().includes(query))
      .map((o) => ({ kind: "value", field: tokenField, ...o }));
  } else {
    items = fields
      .filter((f) => !values[f.key] && qualifier(f).startsWith(word))
      .map((field) => ({ kind: "key", field }));
  }

  const edit = (next: string) => {
    setDraft(next);
    setOpen(true);
    const m = next.match(TOKEN);
    const f = m && fields.find((x) => qualifier(x) === m[1].toLowerCase());
    setActive(f ? 0 : -1);
    onTextChange((f ? next.slice(0, m.index) : next).trim());
  };

  const commit = (field: FilterField, value: string) => {
    onValueChange(field.key, value === field.defaultValue ? undefined : value);
    setDraft(base);
    setActive(-1);
    setOpen(false);
  };

  const select = (item: Item) => {
    if (item.kind === "value") return commit(item.field, item.value);
    setDraft(`${base}${base ? " " : ""}${qualifier(item.field)}:`);
    setActive(0);
    inputRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (items.length === 0) return;
      e.preventDefault();
      setOpen(true);
      setActive((a) => (a + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length);
    } else if (e.key === "Enter") {
      if (open && items[active]) {
        e.preventDefault();
        select(items[active]);
      } else if (tokenField?.user && query) {
        e.preventDefault();
        commit(tokenField, query);
      } else {
        setOpen(false);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Backspace" && draft === "" && removable.length > 0) {
      onValueChange(removable[removable.length - 1].f.key, undefined);
    }
  };

  return (
    <Popover open={open && (items.length > 0 || !!tokenField?.user)} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div
          ref={barRef}
          onClick={() => inputRef.current?.focus()}
          className="flex min-h-[34px] min-w-[240px] flex-1 basis-[360px] flex-wrap items-center gap-1.5 rounded-[9px] border border-border px-2.5 py-1 focus-within:ring-1 focus-within:ring-ring"
        >
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          {chips.map(({ f, value }) => {
            const shown = f.user ? `@${value}` : (f.options?.find((o) => o.value === value)?.label ?? value);
            return (
              <span key={f.key} className="inline-flex items-center gap-1 rounded-md bg-secondary px-1.5 py-0.5 text-xs">
                <span className="text-muted-foreground">{qualifier(f)}:</span>
                {shown}
                {value !== f.defaultValue && (
                  <button
                    type="button"
                    aria-label={`Remove ${f.label} filter`}
                    className="text-muted-foreground hover:text-foreground"
                    onClick={(e) => {
                      e.stopPropagation();
                      onValueChange(f.key, undefined);
                    }}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </span>
            );
          })}
          <input
            ref={inputRef}
            aria-label={placeholder}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-activedescendant={open && items[active] ? `${listId}-${active}` : undefined}
            aria-autocomplete="list"
            placeholder={removable.length === 0 && !draft ? placeholder : ""}
            value={draft}
            onChange={(e) => edit(e.target.value)}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
            className="min-w-[80px] flex-1 border-0 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
          />
          {(removable.length > 0 || draft) && (
            <button
              type="button"
              aria-label="Clear all filters"
              className="text-muted-foreground hover:text-foreground"
              onClick={(e) => {
                e.stopPropagation();
                setDraft("");
                onClear();
              }}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] min-w-[280px] p-1"
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
        onInteractOutside={(e) => {
          if (barRef.current?.contains(e.target as Node)) e.preventDefault();
        }}
      >
        <div id={listId} role="listbox" className="max-h-72 overflow-y-auto">
          {tokenField?.user && items.length === 0 && (
            <div className="px-2.5 py-2 text-xs text-muted-foreground">
              {query.length < 2 ? "Type a username" : `Press Enter to filter by @${query}`}
            </div>
          )}
          {items.map((item, i) => (
            <button
              key={item.kind === "key" ? item.field.key : item.value}
              id={`${listId}-${i}`}
              type="button"
              role="option"
              aria-selected={i === active}
              // Keep focus in the input so typing continues.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => select(item)}
              className={cn(
                "flex w-full items-baseline justify-between gap-3 rounded px-2.5 py-1.5 text-left text-xs hover:bg-accent",
                i === active && "bg-accent",
              )}
            >
              {item.kind === "key" ? (
                <>
                  <span className="font-mono">{qualifier(item.field)}:</span>
                  <span className="truncate text-[11px] text-muted-foreground">{item.field.hint}</span>
                </>
              ) : (
                <>
                  <span className="truncate">{item.label}</span>
                  {item.hint && <span className="shrink-0 text-[11px] text-muted-foreground">{item.hint}</span>}
                </>
              )}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
