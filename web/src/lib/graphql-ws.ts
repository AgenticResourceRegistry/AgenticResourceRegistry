// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createClient, type Client } from "graphql-ws";
import { clearSession, getAccessToken, authEpoch, refreshAccessTokenWithReason } from "@/lib/api";
import { sessionExpiredLoginUrl } from "@/lib/safe-next";

function getWsUrl(): string {
  const api = import.meta.env.VITE_API_URL ||
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:3000");
  return api.replace(/^http/, "ws") + "/api/v1/graphql";
}

const clients = new Set<Client>();
function dropClient(client: Client) {
  clients.delete(client);
  void Promise.resolve(client.dispose()).catch(() => {});
}
if (typeof window !== "undefined") window.addEventListener("observal:session-cleared", () => {
  for (const client of clients) dropClient(client);
});

function getClient(): Client {
  const client = createClient({
      url: getWsUrl(),
      connectionParams: () => {
        const token = getAccessToken();
        return token ? { authorization: `Bearer ${token}` } : {};
      },
      lazy: true,
      retryAttempts: 5,
    });
  clients.add(client);
  return client;
}

function isAuthError(err: unknown): boolean {
  const msg = (Array.isArray(err)
    ? err.map((item: { message?: string }) => item.message ?? String(item)).join(" ")
    : err && typeof err === "object" && "reason" in err
      ? String(err.reason)
      : err instanceof Error ? err.message : String(err ?? "")).toLowerCase();
  return msg.includes("not authenticated") || msg.includes("unauthorized") ||
    msg.includes("invalid token") || msg.includes("token expired");
}

// An old bearer error after a different tab rotated the refresh JTI must not
// invalidate that tab's session. Recover this tab's bearer, then subscribe anew.
function subscribe(
  payload: Parameters<Client["subscribe"]>[0],
  onNext: (value: Parameters<Parameters<Client["subscribe"]>[1]["next"]>[0]) => void,
): () => void {
  const epoch = authEpoch();
  let token: string | null;
  let refresh: string | null;
  let stopped = false;
  let unsubscribe: (() => void) | undefined;
  let socket = getClient();
  const start = () => {
    token = getAccessToken();
    refresh = localStorage.getItem("observal_refresh_token");
    unsubscribe = socket.subscribe(payload, {
      next: (value) => { if (!stopped && epoch === authEpoch()) onNext(value); },
      complete: () => {},
      error: (err) => { void handleError(err); },
    });
  };
  const handleError = async (err: unknown) => {
    if (stopped || !epoch || epoch !== authEpoch() || !token || getAccessToken() !== token) return;
    if (!isAuthError(err)) { console.error("GraphQL subscription failed", err); return; }
    if (localStorage.getItem("observal_refresh_token") !== refresh) {
      try {
        const result = await refreshAccessTokenWithReason();
        if (stopped || epoch !== authEpoch()) return;
        if (result === "ok") {
          dropClient(socket);
          socket = getClient();
          start();
          return;
        }
        // A failed recovery cannot attribute the old socket's auth error to
        // the rotated session; report the closed subscription instead.
        console.error("GraphQL subscription recovery failed", err);
        return;
      } catch {
        if (stopped || epoch !== authEpoch()) return;
        console.error("GraphQL subscription recovery failed", err);
        return;
      }
    }
    // A concurrent login/rotation must never be blamed for this socket's error.
    if (getAccessToken() !== token || localStorage.getItem("observal_refresh_token") !== refresh) return;
    clearSession();
    window.location.href = sessionExpiredLoginUrl();
  };
  start();
  return () => { stopped = true; unsubscribe?.(); dropClient(socket); };
}

export function subscribeToSessionUpdates(
  onEvent: (sessionId: string, eventName: string) => void,
): () => void {
  return subscribe(
    { query: `subscription SessionUpdated($sessionId: String) {
        sessionUpdated(sessionId: $sessionId) { sessionId eventName }
      }` },
    (value) => {
      const data = (value.data as { sessionUpdated?: { sessionId: string; eventName: string } })?.sessionUpdated;
      if (data) onEvent(data.sessionId, data.eventName);
    },
  );
}

export function subscribeToReviewId(
  reviewId: string,
  onEvent: (event: { number: number | null; state: string | null; action: string }) => void,
): () => void {
  return subscribe(
    { query: `subscription ReviewUpdated($reviewId: String) {
        reviewUpdated(reviewId: $reviewId) { number state action }
      }`, variables: { reviewId } },
    (value) => {
      const data = (value.data as { reviewUpdated?: { number: number | null; state: string | null; action: string } })?.reviewUpdated;
      if (data) onEvent(data);
    },
  );
}

export function subscribeToReviewUpdates(
  onEvent: (listingId: string, action: string) => void,
): () => void {
  return subscribe(
    { query: `subscription ReviewUpdated($listingId: String) {
        reviewUpdated(listingId: $listingId) { listingId action }
      }` },
    (value) => {
      const data = (value.data as { reviewUpdated?: { listingId: string; action: string } })?.reviewUpdated;
      if (data) onEvent(data.listingId, data.action);
    },
  );
}
