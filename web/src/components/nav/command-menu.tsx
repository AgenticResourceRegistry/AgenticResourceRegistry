// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useEffect, useState } from "react";
import { useRouter } from "@tanstack/react-router";
import { Search } from "lucide-react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { allNavItems } from "./registry-sidebar";

export function CommandMenu() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { leaderboardEnabled } = useDeploymentConfig();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const onSelect = (href: string) => {
    setOpen(false);
    router.navigate({ to: href });
  };

  return (
    <>
      {/* Mockup search-button pill: wide, left-aligned label, kbd shortcut */}
      <button
        onClick={() => setOpen(true)}
        className="hidden sm:inline-flex items-center justify-start gap-2 w-[min(280px,24vw)] min-h-[34px] px-3.5 rounded-[9px] text-xs font-normal text-muted-foreground transition-colors hover:bg-surface-raised hover:text-foreground"
      >
        <Search className="h-4 w-4 shrink-0" />
        <span className="truncate">Navigate</span>
        <kbd className="ml-auto shrink-0 rounded-[5px] bg-surface-raised px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
          ⌘K
        </kbd>
      </button>
      {/* Compact icon-only trigger on mobile */}
      <button
        onClick={() => setOpen(true)}
        className="inline-flex sm:hidden h-[34px] w-[34px] items-center justify-center rounded-[9px] text-foreground/65 hover:bg-surface-raised hover:text-foreground"
        aria-label="Open navigation"
      >
        <Search className="h-4 w-4" />
      </button>

      <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Find a page or action..." />
      <CommandList>
        <CommandEmpty>No pages or actions found.</CommandEmpty>
        <CommandGroup heading="Navigate">
          {allNavItems.map((group) =>
            group.items
              .filter((item) => item.href !== "/leaderboard" || leaderboardEnabled)
              .map((item) => (
                <CommandItem
                  key={item.href}
                  onSelect={() => onSelect(item.href)}
                >
                  <item.icon className="mr-2 h-4 w-4" />
                  {item.title}
                </CommandItem>
              )),
          )}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Quick Actions">
          <CommandItem onSelect={() => onSelect("/agents/builder")}>
            <span className="mr-2 text-sm">+</span>
            New Agent
          </CommandItem>
          <CommandItem onSelect={() => onSelect("/agents?search=")}>
            <span className="mr-2 text-sm">?</span>
            Search Agents
          </CommandItem>
          <CommandItem onSelect={() => onSelect("/components?search=")}>
            <span className="mr-2 text-sm">?</span>
            Search Components
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
    </>
  );
}
