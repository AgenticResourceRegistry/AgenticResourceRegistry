// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { prReviews } from "@/lib/api";

/** The teamspace Review tab links to participant-scoped review pages. */
export function useTeamReviewQueue(teamId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["pr-reviews", "team", teamId],
    enabled: !!teamId && enabled,
    queryFn: () => prReviews.list({ team_id: teamId!, state: "open", limit: "100" }).then(result => result.items),
  });
}

/** Public queue events contain no private review content. */
export function useReviewSubscription() {
  const qc = useQueryClient();
  const debounce = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    let closed = false;
    let unsubscribe: (() => void) | undefined;
    import("@/lib/graphql-ws").then(({ subscribeToReviewUpdates }) => {
      if (closed) return;
      unsubscribe = subscribeToReviewUpdates(() => {
        clearTimeout(debounce.current);
        debounce.current = setTimeout(() => qc.invalidateQueries({ queryKey: ["pr-reviews"] }), 300);
      });
    });
    return () => { closed = true; clearTimeout(debounce.current); unsubscribe?.(); };
  }, [qc]);
}
