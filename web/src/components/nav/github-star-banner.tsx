// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useCallback, useSyncExternalStore } from "react";
import { Star, X } from "lucide-react";

const DISMISSED_KEY = "observal_github_star_dismissed";

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

export function GitHubStarBanner() {
  const dismissed = useSyncExternalStore(
    subscribe,
    () => localStorage.getItem(DISMISSED_KEY) === "1",
    () => true,
  );

  const dismiss = useCallback(() => {
    localStorage.setItem(DISMISSED_KEY, "1");
    window.dispatchEvent(new StorageEvent("storage"));
  }, []);

  if (dismissed) return null;

  return (
    <div className="group/star flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
      <a
        href="https://github.com/AgenticResourceRegistry/AgenticResourceRegistry"
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1.5"
      >
        <Star className="h-3.5 w-3.5 text-yellow-400 transition-colors group-hover/star:fill-yellow-400" />
        <span>Star us on GitHub</span>
      </a>
      <button
        onClick={dismiss}
        className="ml-0.5 rounded-sm p-0.5 hover:bg-muted"
        aria-label="Dismiss"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
