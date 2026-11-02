// <moxi-repo-page route="...">: repo's panel, read-only. For each indexed repo: its
// problems, each event with its pieces in order, the existing git hook and the chain
// line's state, each committer spec's last pass, last commit and pushes, and the doctor
// records repo has open. The page runs nothing: every action is one of `moxi repo`'s
// commands, shown to copy and run in a terminal. It reads `/api/repos` when it opens,
// when its tab is shown again, and on "Read again".

import { css, html, LitElement, nothing } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { api } from "/m/ui/client.js";

import { Feed } from "./feed.ts";
import type { Command, EventRow, PieceRow, RecordRow, RepoRow, SpecRow } from "./feed.ts";
import {
  chainText, existingText, pageView, pieceTags, piecesOf, recordSubject, repoFacts, settingText,
  specLines, viaText,
} from "./views.ts";

const TAG = "moxi-repo-page";

const STYLES = css`
  :host { display: block; min-width: 0; color: var(--moxi-text); font-family: var(--moxi-font-sans); }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 var(--moxi-gap); font-size: 1.4em; }
  h2 { margin: 0; font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .block { display: block; padding: 6px 8px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
           white-space: pre-wrap; word-break: break-all; }
  .lede { margin-bottom: var(--moxi-gap); padding-left: 8px; border-left: 3px solid var(--moxi-accent); color: var(--moxi-text-dim); }
  .dim { color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  .warn { color: var(--moxi-warn); }
  .fail, .problem-line { color: var(--moxi-error); }
  .ok { color: var(--moxi-ok); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); margin-bottom: var(--moxi-gap); }
  section { display: grid; gap: var(--moxi-gap); min-width: 0; margin-top: calc(var(--moxi-gap) * 2); padding: 12px;
            border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  ul { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; min-width: 0; }
  .part { display: grid; gap: 4px; min-width: 0; padding-top: var(--moxi-gap); border-top: 1px solid var(--moxi-border); }
  .item { display: grid; gap: 2px; min-width: 0; padding: 4px 0 4px 8px; border-left: 3px solid var(--moxi-border); }
  .item.off { opacity: 0.7; }
  .item.fail { border-left-color: var(--moxi-error); }
  .item.warn { border-left-color: var(--moxi-warn); }
  .first { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--moxi-gap); min-width: 0; }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); background: var(--moxi-bg-sunken);
         font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  .tag.bad { background: var(--moxi-warn-soft); color: var(--moxi-warn); }
  button { font: inherit; color: var(--moxi-text); padding: 2px 10px; border: 1px solid var(--moxi-border-strong);
           border-radius: var(--moxi-radius); background: var(--moxi-bg-raised); cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { cursor: not-allowed; opacity: 0.6; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  .copy { display: grid; gap: 4px; min-width: 0; }
  .copy .row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--moxi-gap); }
  details { min-width: 0; }
  details > summary { cursor: pointer; color: var(--moxi-text-dim); font-size: var(--moxi-font-size-sm); }
  details[open] > summary { margin-bottom: 4px; }
  details > .copy + .copy { margin-top: 6px; }
`;

export class RepoPage extends LitElement {
  static styles = STYLES;

  static properties = {
    route: { type: String },
  };

  route = "";
  #feed: Feed | null = null;
  #copied: string | null = null;
  #shown = (): void => {
    if (document.visibilityState === "visible") void this.#feed?.refresh();
  };

  connectedCallback(): void {
    super.connectedCallback();
    this.#feed = new Feed(() => api("repo", "api/repos"), () => this.requestUpdate());
    void this.#feed.refresh();
    this.requestUpdate();
    document.addEventListener("visibilitychange", this.#shown);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#feed?.close();
    this.#feed = null;
    document.removeEventListener("visibilitychange", this.#shown);
  }

  async #copy(text: string, key: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.#copied = key;
    } catch {
      this.#copied = `!${key}`;
    }
    this.requestUpdate();
  }

  render() {
    const feed = this.#feed;
    if (feed === null) return nothing;
    const view = pageView(feed);
    if (view.state === "gone") return nothing;
    const now = feed.now();
    return html`
      <h1>Git hooks</h1>
      <p class="lede">Each repo repo writes git hooks for: the pieces each git event runs, in order, the existing git hook, the committer's specs and the doctor records repo has open. This page changes nothing. Each command here is one to copy and run in a terminal.</p>
      <div class="bar">
        <button data-action="read" ?disabled=${feed.reading} @click=${() => void feed.refresh()}>Read again</button>
        ${feed.reading && !feed.loading ? html`<span class="dim">Reading…</span>` : nothing}
      </div>
      <div data-state=${view.state}>${this.#status(view.state, view.detail)}</div>
      ${repeat(view.repos, (repo) => repo.root, (repo) => this.#repo(repo, now))}`;
  }

  #status(state: string, detail: string | null) {
    switch (state) {
      case "loading":
        return html`<p class="dim">Reading the repos…</p>`;
      case "failed":
        return html`<p class="problem">The repos can't be read: ${detail}.</p>`;
      case "error":
        return html`<p class="problem">${detail}</p>`;
      case "empty":
        return html`<p class="dim">No repo has git hook pieces yet. See the pieces installed modules declare with <code>moxi repo git-hooks available</code>, and add one with <code>moxi repo git-hooks add PATH PIECE</code>.</p>`;
      default:
        return nothing;
    }
  }

  #copyable(command: Command | string, key: string) {
    const text = typeof command === "string" ? command : command.command;
    const copied = this.#copied === key ? html`<span class="dim">Copied.</span>`
      : this.#copied === `!${key}` ? html`<span class="warn">Select the command and copy it.</span>` : nothing;
    return html`<div class="copy"><code class="block" data-copy=${key}>${text}</code>
      <div class="row"><button data-action="copy" @click=${() => void this.#copy(text, key)}>Copy</button>${copied}</div></div>`;
  }

  #commands(commands: Command[], key: string) {
    return html`<details data-part="commands"><summary>Commands</summary>
      ${commands.map((command) => html`<p class="dim">${command.label}:</p>${this.#copyable(command, `${key}:${command.label}`)}`)}</details>`;
  }

  #repo(repo: RepoRow, now: number | null) {
    const key = repo.root;
    return html`<section data-repo=${repo.root}>
      <h2><code>${repo.root}</code></h2>
      <p class="dim" data-line="facts">${repoFacts(repo).join(" · ")}</p>
      ${repo.problems.length ? html`<div class="part" data-part="problems"><h3>Problems</h3><ul>
        ${repo.problems.map((problem, n) => html`<li class="item ${problem.state}" data-problem=${problem.state}>
          <p class=${problem.state === "ok" ? "" : problem.state}>${problem.detail}</p>
          ${problem.fix ? this.#copyable(problem.fix, `${key}:problem:${n}`) : nothing}</li>`)}</ul></div>` : nothing}
      <div class="part" data-part="events"><h3>Git events</h3>
        ${repo.events.length ? html`<ul>${repo.events.map((event) => this.#event(repo, event))}</ul>`
          : html`<p class="dim">No piece is registered here.</p>`}</div>
      ${this.#specs(repo, now)}
      ${repo.records.length ? html`<div class="part" data-part="records"><h3>Open doctor records</h3><ul>
        ${repo.records.map((record) => this.#record(record))}</ul></div>` : nothing}
      ${this.#commands(repo.commands, key)}
    </section>`;
  }

  #event(repo: RepoRow, event: EventRow) {
    const chain = chainText(event);
    const key = `${repo.root}:${event.event}`;
    return html`<li class="item" data-event=${event.event}>
      <div class="first"><strong>${event.event}</strong><span class="dim">${viaText(event)}</span></div>
      <ul>${piecesOf(repo, event).map((piece) => this.#piece(repo, piece))}</ul>
      <p class="dim" data-line="existing">${existingText(event)}</p>
      ${chain ? html`<p data-line="chain" class=${event.chain_line === "needed" ? "warn" : "dim"}>${chain}</p>` : nothing}
      ${event.chain_line === "needed" && event.line ? this.#copyable(event.line, `${key}:chain`) : nothing}
    </li>`;
  }

  #piece(repo: RepoRow, piece: PieceRow) {
    return html`<li class="item ${piece.enabled ? "" : "off"}" data-piece=${piece.id}>
      <div class="first"><code>${piece.id}</code>
        ${pieceTags(piece).map((tag) => html`<span class="tag ${tag.startsWith("fail") || tag.startsWith("order") ? "" : "bad"}">${tag}</span>`)}</div>
      ${piece.settings.length ? html`<p class="dim" data-line="settings">${piece.settings.map(settingText).join("; ")}</p>` : nothing}
      ${this.#commands([piece.toggle, piece.remove], `${repo.root}:piece:${piece.id}`)}
    </li>`;
  }

  #specs(repo: RepoRow, now: number | null) {
    if (repo.specs_problem) {
      return html`<div class="part" data-part="specs"><h3>Committer specs</h3><p class="warn">Its specs can't be listed: ${repo.specs_problem}</p></div>`;
    }
    if (!repo.specs.length) return nothing;
    return html`<div class="part" data-part="specs"><h3>Committer specs</h3><ul>
      ${repo.specs.map((spec) => this.#spec(repo, spec, now))}</ul></div>`;
  }

  #spec(repo: RepoRow, spec: SpecRow, now: number | null) {
    const bad = spec.problem || spec.push_failed.length || spec.status === "refused" || spec.status === "error";
    return html`<li class="item ${bad ? "warn" : ""}" data-spec=${spec.name}>
      <div class="first"><code>${spec.name}</code><span class="tag">${spec.saved ? "saved" : "not saved"}</span>
        ${spec.status ? html`<span class="tag ${spec.status === "refused" || spec.status === "error" ? "bad" : ""}" data-status=${spec.status}>${spec.status}</span>` : nothing}</div>
      ${specLines(spec, now).map((line) => html`<p class="dim">${line}</p>`)}
      ${this.#commands([spec.run, spec.forget], `${repo.root}:spec:${spec.name}`)}
    </li>`;
  }

  #record(record: RecordRow) {
    return html`<li class="item fail" data-record=${record.kind}>
      <p><strong>${recordSubject(record)}</strong>${record.at ? html` <span class="dim">at ${record.at}</span>` : nothing}</p>
      <p class="block">${record.text}</p>
      ${record.fix ? html`<p class="dim">Fix: ${record.fix}</p>` : nothing}
    </li>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, RepoPage);
