// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createClient, type Client, type SubscribePayload } from "graphql-ws";
import { clearTabSession, getTabActor, isAccountChanged, quarantineTab, tokenSubject } from "@/lib/api";
import { sessionExpiredLoginUrl } from "@/lib/safe-next";

function getWsUrl(): string {
  const api =
    import.meta.env.VITE_API_URL ||
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:3000");
  return api.replace(/^http/, "ws") + "/api/v1/graphql";
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("observal_access_token");
}

let client: Client | null = null;
let clientToken: string | null = null;

function handleSubscriptionAuthError(err: unknown, token: string | null, actor: string | null) {
  if (isAccountChanged()) { quarantineTab(); return; }
  if (!actor || tokenSubject(token) !== actor || getTabActor() !== actor || getToken() !== token) return;
  const msg = String(err ?? "").toLowerCase();
  if (
    msg.includes("not authenticated") ||
    msg.includes("unauthorized") ||
    msg.includes("invalid token") ||
    msg.includes("token expired")
  ) {
    clearTabSession();
    if (typeof window !== "undefined") {
      window.location.href = sessionExpiredLoginUrl();
    }
  }
}

function getClient(): Client {
  const token = getToken();
  if (client && clientToken !== token) {
    void client.dispose();
    client = null;
  }
  if (!client) {
    clientToken = token;
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

// Mounted subscriptions must follow same-actor token rotation. A subscription
// opened by A must never reconnect under B, even when its component stays mounted.
function subscribeForActor(payload: SubscribePayload, onData: (value: Record<string, unknown>) => void): () => void {
  const actor = getTabActor();
  let token = getToken();
  let unsubscribe: (() => void) | undefined;
  let stopped = false;

  const start = () => {
    if (!token || !actor || tokenSubject(token) !== actor || isAccountChanged()) return;
    const subscriptionToken = token;
    unsubscribe = getClient().subscribe(payload, {
      next: (value) => {
        if (getTabActor() === actor && getToken() === subscriptionToken && !isAccountChanged()) {
          onData((value.data ?? {}) as Record<string, unknown>);
        }
      },
      error: (err) => handleSubscriptionAuthError(err, subscriptionToken, actor),
      complete: () => {},
    });
  };
  const onStorage = () => {
    if (stopped) return;
    const current = getToken();
    if (getTabActor() !== actor || isAccountChanged()) {
      unsubscribe?.();
      unsubscribe = undefined;
      window.removeEventListener("storage", onStorage);
      return;
    }
    if (current === token) return;
    unsubscribe?.();
    unsubscribe = undefined;
    token = current;
    start();
  };
  window.addEventListener("storage", onStorage);
  start();
  return () => {
    stopped = true;
    window.removeEventListener("storage", onStorage);
    unsubscribe?.();
  };
}

export function subscribeToSessionUpdates(
  onEvent: (sessionId: string, eventName: string) => void,
): () => void {
  return subscribeForActor(
    {
      query: `subscription SessionUpdated($sessionId: String) {
        sessionUpdated(sessionId: $sessionId) {
          sessionId
          eventName
        }
      }`,
    },
    (value) => {
      const data = value.sessionUpdated as { sessionId: string; eventName: string } | undefined;
      if (data) onEvent(data.sessionId, data.eventName);
    },
  );
}

export function subscribeToReviewId(
  reviewId: string,
  onEvent: (event: { number: number | null; state: string | null; action: string }) => void,
): () => void {
  return subscribeForActor(
    {
      query: `subscription ReviewUpdated($reviewId: String) {
        reviewUpdated(reviewId: $reviewId) { number state action }
      }`,
      variables: { reviewId },
    },
    (value) => {
      const data = value.reviewUpdated as { number: number | null; state: string | null; action: string } | undefined;
      if (data) onEvent(data);
    },
  );
}

export function subscribeToReviewUpdates(
  onEvent: (listingId: string, action: string) => void,
): () => void {
  return subscribeForActor(
    {
      query: `subscription ReviewUpdated($listingId: String) {
        reviewUpdated(listingId: $listingId) {
          listingId
          action
        }
      }`,
    },
    (value) => {
      const data = value.reviewUpdated as { listingId: string; action: string } | undefined;
      if (data) onEvent(data.listingId, data.action);
    },
  );
}
