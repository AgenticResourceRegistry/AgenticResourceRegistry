// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { GitPullRequest } from "lucide-react";
import { prReviews } from "@/lib/api";

/** The subject-ID lookup resolves the newest open review, including a pending new version. */
export function ActiveReviewBanner({ subjectId, enabled }: { subjectId: string; enabled: boolean }) {
  const { data: review } = useQuery({
    queryKey: ["pr-review", "subject", subjectId],
    queryFn: () => prReviews.detail(subjectId),
    enabled: enabled && !!subjectId,
    retry: false,
  });
  if (!review) return null;
  return <div className="flex flex-wrap items-center gap-3 rounded-lg border border-info/30 bg-info/5 px-4 py-3 text-sm">
    <GitPullRequest className="size-4 shrink-0 text-info" aria-hidden />
    <span className="flex-1">Review <strong>#{review.number}</strong> · {review.state.replaceAll("_", " ")}
      {review.threads.unresolved > 0 && ` · ${review.threads.unresolved} unresolved conversation${review.threads.unresolved === 1 ? "" : "s"}`}
    </span>
    <Link to="/review/$number" params={{ number: String(review.number) }} className="font-medium text-info underline-offset-4 hover:underline focus-visible:underline">Open review</Link>
  </div>;
}
