// SPDX-FileCopyrightText: 2026 Deepesh Kumar <akadeepesh1710@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from "react";
import { ErrorBoundary } from "@/components/shared/error-boundary";
import { ErrorState } from "@/components/shared/error-state";

interface CardErrorBoundaryProps {
  children: ReactNode;
  /** Layout classes for the fallback, so it takes the card's place in the grid (e.g. `lg:col-span-2`). */
  className?: string;
}

/** Stops one dashboard card or tab that throws while rendering from taking down the whole page. */
export function CardErrorBoundary({ children, className }: CardErrorBoundaryProps) {
  return (
    <ErrorBoundary
      fallback={({ reset }) => (
        <div className={className}>
          <ErrorState message="This section failed to load." onRetry={reset} />
        </div>
      )}
    >
      {children}
    </ErrorBoundary>
  );
}
