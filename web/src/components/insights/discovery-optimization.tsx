// SPDX-FileCopyrightText: 2026 Observal Contributors
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { DiscoveryOptimization } from "@/lib/types";

export function DiscoveryOptimizationSection({ requested, result }: {
  requested?: boolean;
  result?: DiscoveryOptimization | null;
}) {
  if (!requested) return null;
  return (
    <section aria-label="ARD discovery suggestions" className="space-y-4 rounded-md border border-border bg-card p-5">
      <h2 className="text-lg font-semibold">ARD discovery suggestions</h2>
      {!result || result.state === "unavailable" ? (
        <p className="text-sm text-muted-foreground">
          Suggestions are unavailable for this report. The usage analysis is unaffected.
        </p>
      ) : result.state === "unknown" ? (
        <p className="text-sm text-muted-foreground">
          No suggestion passed the accuracy checks, so none are shown. The usage analysis is unaffected.
        </p>
      ) : !result.suggestions?.length ? (
        <p className="text-sm text-muted-foreground">No supported description edits were identified. This does not measure search traffic.</p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Illustrative searches, not recorded user queries. Advice uses {result.metadata_version_scope === "current_listing" ? "the current listing" : "the selected listing version"}{result.listing_version ? ` (v${result.listing_version})` : ""}; an all-versions report may include earlier usage. Review every suggestion for accuracy before editing; no ranking is guaranteed.</p>
          <ol className="space-y-4">
            {result.suggestions.map((item, index) => (
              <li key={`${item.illustrative_query}-${index}`} className="space-y-2 border-t border-border pt-4">
                <p className="text-sm"><strong>Example search:</strong> {item.illustrative_query}</p>
                <p className="text-sm">{item.rationale}</p>
                <p className="text-sm"><strong>Suggested description:</strong> {item.suggested_description}</p>
                <p className="text-xs text-muted-foreground">
                  ARD relevance for this example: {item.relevance_before} → {item.relevance_after} (not a search position)
                  {item.evidence_keys.length ? ` · Aggregated session signals: ${item.evidence_keys.join(", ")}` : " · No session claim"}
                  {item.listing_evidence_keys?.length ? ` · Listing fields: ${item.listing_evidence_keys.join(", ")}` : ""}
                </p>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
