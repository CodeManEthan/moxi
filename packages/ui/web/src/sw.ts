// The gateway's worker, /sw.js, scope `/`. It shows the offline page when a navigation
// can't reach the gateway, and sends every other same-origin GET on.
//
// Nothing it answers comes from storage a page under this origin can write, a page another
// server left there included: it answers from the network or from its own bytes. Every GET
// it sends on goes with `cache: "reload"`, navigations included, so the browser's HTTP
// cache doesn't answer for it either. The offline page and its script are built into this
// file. The one value it keeps, when the gateway last answered, it reads only as a number.

import type { Reach } from "./gateway-model.ts";
import {
  ANSWERED, answerReach, answeredAt, CACHE, handling, NAVIGATION_HEADER, navigationReach,
  OFFLINE_SCRIPT, offlineDocument, ownHeaders,
} from "./worker-model.ts";

// The offline page and its built script, put into this file by the UI module's build.
declare const OFFLINE_HTML: string;
declare const OFFLINE_JS: string;

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

/** How often, at most, the time the gateway last answered is written to the cache. */
const NOTE_MS = 10_000;

let answered: number | null = null;
let noted = 0;

/** Notes that the gateway answered now. */
async function heard(): Promise<void> {
  const now = Date.now();
  answered = now;
  if (now - noted < NOTE_MS) return;
  noted = now;
  const cache = await caches.open(CACHE);
  await cache.put(ANSWERED, new Response(String(now)));
}

/** When the gateway last answered: this worker's note, else the one it kept, read only as
 * a time in range; null when neither says. */
async function lastAnswered(): Promise<number | null> {
  if (answered !== null) return answered;
  try {
    const kept = await (await caches.open(CACHE)).match(ANSWERED);
    return kept === undefined ? null : answeredAt(await kept.text(), Date.now());
  } catch {
    return null;
  }
}

/** The offline page for `reach`, from this file's own copy. */
async function offline(reach: Reach): Promise<Response> {
  const html = offlineDocument(OFFLINE_HTML, reach, await lastAnswered());
  return new Response(html, { status: 200, headers: ownHeaders("text/html; charset=utf-8") });
}

/** One of the worker's own files: the offline page, read as the computer out of reach,
 * or its script. */
async function own(path: string): Promise<Response> {
  if (path === OFFLINE_SCRIPT) {
    return new Response(OFFLINE_JS, { status: 200, headers: ownHeaders("text/javascript; charset=utf-8") });
  }
  return await offline("away");
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
  return answer;
}

/** Any other same-origin GET: sent on past the HTTP cache. The network's answer, or its
 * failure. */
async function get(event: FetchEvent): Promise<Response> {
  const request = event.request;
  const answer = await fetch(new Request(request, { cache: request.cache === "no-store" ? "no-store" : "reload" }));
  if (answerReach(answer.status, answer.headers.get("Content-Type")) === "up") event.waitUntil(heard());
  return answer;
}

scope.addEventListener("install", (event) => {
  event.waitUntil(scope.skipWaiting());
});

/** Deletes every cache but the worker's, and in it every entry but the time it keeps. */
async function tidy(): Promise<void> {
  for (const name of await caches.keys()) {
    if (name !== CACHE) await caches.delete(name);
  }
  const cache = await caches.open(CACHE);
  for (const request of await cache.keys()) {
    if (new URL(request.url).pathname !== ANSWERED) await cache.delete(request);
  }
}

scope.addEventListener("activate", (event) => {
  event.waitUntil(tidy().catch(() => undefined).then(() => scope.clients.claim()));
});

scope.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  const how = handling(request.method, url, scope.location.origin, request.mode);
  if (how === "pass") return;
  if (how === "own") event.respondWith(own(url.pathname));
  else event.respondWith(how === "navigation" ? navigation(event) : get(event));
});
