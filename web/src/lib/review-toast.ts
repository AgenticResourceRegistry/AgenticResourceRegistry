// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { toast } from "sonner";

/** Give authors a direct path from a successful submission to its review. */
/** Tell the author when a draft save became a new revision of the open review. */
export function draftSavedToast(result: { review_number?: number | null; review_revision?: number | null }) {
  if (result.review_number && result.review_revision) {
    reviewToast(`Draft saved. Review #${result.review_number} now shows it as revision ${result.review_revision}.`, result);
  } else {
    toast.success("Draft updated");
  }
}

export function reviewToast(message: string, result: { review_number?: number | null }) {
  const number = result.review_number;
  toast.success(message, number ? {
    action: { label: `View #${number}`, onClick: () => window.location.assign(`/review/${number}`) },
  } : undefined);
}
