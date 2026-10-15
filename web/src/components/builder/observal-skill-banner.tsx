// SPDX-FileCopyrightText: 2026 SK Akram <skcodewizard786@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useSyncExternalStore } from "react";
import { Link } from "@tanstack/react-router";
import { Lightbulb, X } from "lucide-react";

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
      className="mb-4 flex items-start gap-3 rounded-lg border border-info/20 bg-info/5 px-4 py-3"
    >
      <Lightbulb aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-info" />
      <div className="min-w-0 flex-1 space-y-1 text-sm">
        <h2 id="observal-skill-recommendation-title" className="font-medium text-foreground">
          Recommended: create agents in your harness
        </h2>
        <p className="text-muted-foreground">
          Let your harness use the Observal skill to create agents directly. Try:{" "}
          <span className="font-medium text-foreground">“Create an agent that uses the GitHub MCP.”</span>
        </p>
        <p className="text-muted-foreground">
          <Link
            to="/wiki"
            search={{ doc: "getting-started/observal-skill.md" }}
            className="rounded-sm text-primary underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
          >
            Learn about the Observal skill
          </Link>
          {" "}You can also continue with the manual Builder below.
        </p>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss Observal skill recommendation"
        className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        <X aria-hidden="true" className="h-4 w-4" />
      </button>
    </section>
  );
}
