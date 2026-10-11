// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { toast } from "sonner";

/** Give authors a direct path from a successful submission to its review. */
export function reviewToast(message: string, result: { review_number?: number | null }) {
  const number = result.review_number;
  toast.success(message, number ? {
    action: { label: `View #${number}`, onClick: () => window.location.assign(`/review/${number}`) },
  } : undefined);
}
