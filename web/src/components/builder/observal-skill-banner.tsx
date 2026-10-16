// SPDX-FileCopyrightText: 2026 SK Akram <skcodewizard786@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useSyncExternalStore } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowUpRight, Info } from "lucide-react";

const DISMISSED_KEY = "observal_builder_skill_banner_dismissed";

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function isDismissed() {
  try {
    return localStorage.getItem(DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function ObservalSkillBanner() {
  const storedDismissal = useSyncExternalStore(subscribe, isDismissed, () => true);
  const [dismissedThisVisit, setDismissedThisVisit] = useState(false);

  function dismiss() {
    setDismissedThisVisit(true);
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
      window.dispatchEvent(new StorageEvent("storage", { key: DISMISSED_KEY }));
    } catch {
      // Dismiss for this visit even when browser storage is unavailable.
    }
  }

  if (storedDismissal || dismissedThisVisit) return null;

  return (
    <section
      aria-labelledby="observal-skill-recommendation-title"
      className="mb-4 rounded-xl bg-card px-5 py-4 shadow-sm"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
        <div className="min-w-0 flex-1">
          <h2 id="observal-skill-recommendation-title" className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Info aria-hidden="true" className="h-5 w-5 shrink-0 fill-foreground text-card" />
            Build your Agent File in your harness
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Ask your coding assistant to use the Observal skill to create an agent.
          </p>
        </div>
        <Link
          to="/wiki"
          search={{ doc: "getting-started/observal-skill.md" }}
          className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-md text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-ring sm:self-auto"
        >
          How to use the skill
          <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </div>
      <div className="mt-3 border-t border-border pt-2">
        <button
          type="button"
          onClick={dismiss}
          className="rounded-md py-1 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Don’t show this again
        </button>
      </div>
    </section>
  );
}
