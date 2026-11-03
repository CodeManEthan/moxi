// The gateway's worker, /sw.js, scope `/`. It keeps the app shell and the offline page on
// the phone and shows the offline page when a navigation can't reach the gateway.
//
// No answer the browser's HTTP cache holds runs here: the worker fills its cache and sends
// on every same-origin GET it answers with `cache: "reload"`, navigations included, so a
// page or script another server left in that cache under this origin is never read.

import type { Reach } from "./gateway-model.ts";
import {
  ANSWERED, answerReach, CACHE, handling, KEPT, NAVIGATION_HEADER, navigationReach, OFFLINE,
  offlineDocument, useKept,
} from "./worker-model.ts";

// The worker's own globals, which the page's DOM types don't hold.
interface ExtendableEvent extends Event {
  waitUntil(promise: Promise<unknown>): void;
}

interface FetchEvent extends ExtendableEvent {
  readonly request: Request;
  respondWith(answer: Response | Promise<Response>): void;
}

interface WorkerScope {
  readonly location: Location;
  skipWaiting(): Promise<void>;
  readonly clients: { claim(): Promise<void> };
  addEventListener(type: "install" | "activate", fn: (event: ExtendableEvent) => void): void;
  addEventListener(type: "fetch", fn: (event: FetchEvent) => void): void;
}

const scope = self as unknown as WorkerScope;

/** How long the kept files go before a navigation that reached the gateway fetches them
 * again; the ones a page loads are fetched again each time it loads them. */
const REFRESH_MS = 60 * 60 * 1000;
/** How often, at most, the time the gateway last answered is written to the cache. */
const NOTE_MS = 10_000;

let answered: number | null = null;
let noted = 0;
let refreshed = 0;

function fresh(path: string): Request {
  return new Request(path, { cache: "reload", credentials: "same-origin" });
}

/** Keeps the files of the app shell, fetched past the HTTP cache. A file the gateway
 * refuses is left out and kept once a signed-in page loads it: the shell's own files need
 * a session, and a phone may hold none when this worker installs, as when it replaces a
 * worker another server left, whose install must not fail for that. Fails, and so fails
 * the install, when the gateway can't be reached. */
async function keep(): Promise<void> {
  const cache = await caches.open(CACHE);
  await Promise.all(KEPT.map(async (path) => {
    const answer = await fetch(fresh(path));
    if (answer.ok) await cache.put(path, answer);
  }));
  refreshed = Date.now();
}

/** Notes that the gateway answered now. */
async function heard(): Promise<void> {
  const now = Date.now();
  answered = now;
  if (now - noted < NOTE_MS) return;
  noted = now;
  const cache = await caches.open(CACHE);
  await cache.put(ANSWERED, new Response(String(now)));
}

/** When the gateway last answered: this worker's note, else the one it kept. */
async function lastAnswered(): Promise<number | null> {
  if (answered !== null) return answered;
  const kept = await (await caches.open(CACHE)).match(ANSWERED);
  if (kept === undefined) return null;
  const ms = Number(await kept.text());
  return Number.isFinite(ms) ? ms : null;
}

/** The offline page for `reach`, made from the kept one, its headers as the gateway sent
 * them. A network error when the page isn't kept. */
async function offline(reach: Reach): Promise<Response> {
  const kept = await (await caches.open(CACHE)).match(OFFLINE);
  if (kept === undefined) return Response.error();
  const headers = new Headers(kept.headers);
  headers.delete("Content-Length");
  const html = offlineDocument(await kept.text(), reach, await lastAnswered());
  return new Response(html, { status: 200, headers });
}

/** A navigation: sent on past the HTTP cache, marked as a navigation, its redirect left to
 * the browser. The offline page when there's no answer, or the tunnel's or the off one. */
async function navigation(event: FetchEvent): Promise<Response> {
  const headers = new Headers(event.request.headers);
  headers.set(NAVIGATION_HEADER, "1");
  let answer: Response;
  try {
    answer = await fetch(new Request(event.request, { cache: "reload", redirect: "manual", headers }));
  } catch {
    return await offline("away");
  }
  const reach = navigationReach(answer.status, answer.type);
  if (reach !== "up") return await offline(reach);
  event.waitUntil(heard());
  if (Date.now() - refreshed > REFRESH_MS) event.waitUntil(keep().catch(() => undefined));
  return answer;
}

/** Any other same-origin GET: sent on past the HTTP cache. A kept file is kept fresh while
 * the gateway answers, and answered from the cache when it doesn't. */
async function get(event: FetchEvent): Promise<Response> {
  const request = event.request;
  const path = new URL(request.url).pathname;
  const isKept = KEPT.includes(path);
  let answer: Response | null = null;
  try {
    answer = await fetch(new Request(request, { cache: request.cache === "no-store" ? "no-store" : "reload" }));
  } catch {
    answer = null;
  }
  if (answer !== null && answerReach(answer.status, answer.headers.get("Content-Type")) === "up") {
    event.waitUntil(heard());
  }
  if (isKept && useKept(answer?.status ?? null)) {
    const kept = await (await caches.open(CACHE)).match(path);
    if (kept !== undefined) return kept;
  }
  if (answer === null) return Response.error();
  if (isKept && answer.ok) {
    const copy = answer.clone();
    event.waitUntil(caches.open(CACHE).then((cache) => cache.put(path, copy)));
  }
  return answer;
}

scope.addEventListener("install", (event) => {
  event.waitUntil(keep().then(() => scope.skipWaiting()));
});

scope.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== CACHE) await caches.delete(name);
    }
    await scope.clients.claim();
  })());
});

scope.addEventListener("fetch", (event) => {
  const request = event.request;
  const how = handling(request.method, new URL(request.url), scope.location.origin, request.mode);
  if (how === "pass") return;
  event.respondWith(how === "navigation" ? navigation(event) : get(event));
});
