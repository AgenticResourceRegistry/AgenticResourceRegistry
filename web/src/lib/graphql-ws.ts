// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createClient, type Client } from "graphql-ws";
import { clearSession, getAccessToken, authEpoch } from "@/lib/api";
import { sessionExpiredLoginUrl } from "@/lib/safe-next";

function getWsUrl(): string {
  const api =
    import.meta.env.VITE_API_URL ||
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:3000");
  return api.replace(/^http/, "ws") + "/api/v1/graphql";
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return getAccessToken();
}

let client: Client | null = null;
if (typeof window !== "undefined") window.addEventListener("observal:session-cleared", () => {
  const old = client;
  client = null;
  void Promise.resolve(old?.dispose()).catch(() => {});
});

function handleSubscriptionAuthError(err: unknown, epoch: string | null, token: string | null, refresh: string | null) {
  // A rotated bearer can fail after another tab refreshed within the same epoch.
  if (!epoch || authEpoch() !== epoch || !token || getAccessToken() !== token ||
      localStorage.getItem("observal_refresh_token") !== refresh) return;
  const msg = (Array.isArray(err)
    ? err.map((item: { message?: string }) => item.message ?? String(item)).join(" ")
    : err && typeof err === "object" && "reason" in err
      ? String(err.reason)
      : err instanceof Error ? err.message : String(err ?? "")).toLowerCase();
  if (
    msg.includes("not authenticated") ||
    msg.includes("unauthorized") ||
    msg.includes("invalid token") ||
    msg.includes("token expired")
  ) {
    clearSession();
    if (typeof window !== "undefined") {
      window.location.href = sessionExpiredLoginUrl();
    }
  }
}

function getClient(): Client {
  if (!client) {
    client = createClient({
      url: getWsUrl(),
      connectionParams: () => {
        const token = getToken();
        return token ? { authorization: `Bearer ${token}` } : {};
      },
      lazy: true,
      retryAttempts: 5,
    });
  }
  return client;
}

export function subscribeToSessionUpdates(
  onEvent: (sessionId: string, eventName: string) => void,
): () => void {
  const epoch = authEpoch();
  const token = getToken();
  const refresh = localStorage.getItem("observal_refresh_token");
  return getClient().subscribe(
    {
      query: `subscription SessionUpdated($sessionId: String) {
        sessionUpdated(sessionId: $sessionId) {
          sessionId
          eventName
        }
      }`,
    },
    {
      next: (value) => {
        const data = (value.data as { sessionUpdated?: { sessionId: string; eventName: string } })
          ?.sessionUpdated;
        if (epoch === authEpoch() && data) {
          onEvent(data.sessionId, data.eventName);
        }
      },
      error: (err) => handleSubscriptionAuthError(err, epoch, token, refresh),
      complete: () => {},
    },
  );
}

export function subscribeToReviewId(
  reviewId: string,
  onEvent: (event: { number: number | null; state: string | null; action: string }) => void,
): () => void {
  const epoch = authEpoch();
  const token = getToken();
  const refresh = localStorage.getItem("observal_refresh_token");
  return getClient().subscribe(
    {
      query: `subscription ReviewUpdated($reviewId: String) {
        reviewUpdated(reviewId: $reviewId) { number state action }
      }`,
      variables: { reviewId },
    },
    {
      next: (value) => {
        const data = (value.data as { reviewUpdated?: { number: number | null; state: string | null; action: string } })?.reviewUpdated;
        if (epoch === authEpoch() && data) onEvent(data);
      },
      error: (err) => handleSubscriptionAuthError(err, epoch, token, refresh),
      complete: () => {},
    },
  );
}

export function subscribeToReviewUpdates(
  onEvent: (listingId: string, action: string) => void,
): () => void {
  const epoch = authEpoch();
  const token = getToken();
  const refresh = localStorage.getItem("observal_refresh_token");
  return getClient().subscribe(
    {
      query: `subscription ReviewUpdated($listingId: String) {
        reviewUpdated(listingId: $listingId) {
          listingId
          action
        }
      }`,
    },
    {
      next: (value) => {
        const data = (value.data as { reviewUpdated?: { listingId: string; action: string } })
          ?.reviewUpdated;
        if (epoch === authEpoch() && data) {
          onEvent(data.listingId, data.action);
        }
      },
      error: (err) => handleSubscriptionAuthError(err, epoch, token, refresh),
      complete: () => {},
    },
  );
}
