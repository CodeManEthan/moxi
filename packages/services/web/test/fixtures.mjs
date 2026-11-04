// Rows in `/api/rows`' shape, for the Node tests.

/** A written daemon row, with `fields` laid over it. */
export function row(fields = {}) {
  return {
    id: "services/web", module: "services", name: "web", form: "written", kind: "daemon",
    doc: "serves the docs", project: null, launches_session: false, flag: true, switch: null,
    has_switch: true, verdict: { state: "ok", reason: "running", fix: null, unknown: false },
    state: "active", enabled: true, pid: 4242, since: "2026-11-03T09:15:00Z", detail: null,
    last_run: null, checked: null, argv: ["/usr/bin/python3", "-m", "http.server"], cwd: null,
    unit: null, schedule: null, health: null, stopped: null, removing: false, platform: "linux",
    ...fields,
  };
}

/** A written timer row, with `fields` laid over it. */
export function timer(fields = {}) {
  return row({
    id: "services/backup", name: "backup", kind: "timer", state: "inactive", pid: null, since: null,
    schedule: "every 5m", last_run: { failed: false, text: "ok", at: "2026-11-03T10:00:00Z" },
    ...fields,
  });
}

/** A module daemon row. */
export function moduleRow(fields = {}) {
  return row({
    id: "cache/watch", module: "cache", name: "watch", form: "module", doc: "watch the caches",
    switch: "cache.watch.enabled", argv: null, platform: null, ...fields,
  });
}

export function rows(list, fields = {}) {
  return { state: "up", detail: null, rows: list, problems: [], ...fields };
}
