// <moxi-ui-home>: Home, the shell's first page. One main spot, then a row per row
// component that has something to report.
//
// The spot holds the main piece the installed modules announce (`home_as: "main"`), the
// one with the lowest `order`. With none, or with one that did not load, it says
// `Nothing needs you.`, unless a row needs the user, when it stays empty; while a piece
// is loading it says nothing. The phone gets no spot at all: its Home is the rows, under
// the shell's line that says the phone only reads.
//
// A main piece that shows a side panel at the window's right edge says how wide it is with
// a `moxi-home-side` event, and Home moves its column to the centre of what the panel
// leaves, in a window wide enough for both. Home moves it with a margin only: a transform,
// a filter or `contain` here would make Home, not the window, the box a piece's
// `position: fixed` panel is placed in.
//
// A row component (`home_as: "row"`) is placed in a host that stays hidden, as soon as the
// modules list names it. It reports its line with a `moxi-home-row` event and Home draws
// the row, so every row looks the same: the component's title, the parts of the fact, the
// time. Home listens for as long as it is open, so a row shows from its first report
// however late that comes, and goes when the component reports null. A row that opens
// shows its component's host under it. A component whose script does not load shows
// nothing here.

import { css, html, LitElement, nothing, unsafeCSS } from "lit";
import type { PropertyValues } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { components, gateway, installed, navigate, place, subscribe } from "/m/ui/client.js";

import { anyNeeds, readReport, readSide, rowMode, rowOpen, shownRows, SIDE_FROM_PX, sideMargin } from "./home-model.ts";
import type { RowReport } from "./home-model.ts";
import { placeOnceMore, scriptFailure } from "./place-model.ts";
import { homeRows, mainPiece } from "./views.ts";
import type { HomeSeat, ModuleView } from "./views.ts";

const TAG = "moxi-ui-home";
/** The event a row component reports its line with. */
const REPORT = "moxi-home-row";
/** The event the main piece says its side panel's width with. */
const SIDE = "moxi-home-side";
/** Holds the column's left margin while a side panel is shown. */
const SIDE_MARGIN = "--moxi-home-side-margin";

/** One row component on Home. */
interface Row extends HomeSeat {
  key: string;
  /** Where its script is, so a module that changes its script is placed again. */
  url: string;
  /** Holds the component; shown only while the row is open. */
  host: HTMLElement;
  report: RowReport | null;
  /** Opened by a click. */
  clicked: boolean;
}

function scriptOf(seat: { module: string; name: string }): string {
  return components(seat.module).find((comp) => comp.name === seat.name)?.url ?? "";
}

/** Places `piece`'s component in `host`, with one more try for a script whose fetch was
 * dropped; false when it could not be placed or `current` no longer holds. */
async function seat(host: Element, piece: { module: string; name: string }, current: () => boolean): Promise<boolean> {
  const url = scriptOf(piece);
  const load = await placeOnceMore(() => place(host, piece.module, piece.name),
                                   async () => (url === "" ? "no script" : await scriptFailure(piece.module, url)),
                                   current);
  return load !== null && "placed" in load;
}

export class UiHome extends LitElement {
  static styles = css`
    :host { display: block; max-width: 680px; margin: 0 auto; padding-top: 138px; min-width: 0; }
    * { box-sizing: border-box; }
    .spot { min-height: 150px; }
    .quiet { margin: 0; font-size: 22px; line-height: 150px; text-align: center; letter-spacing: -0.005em; }
    .rows { display: flex; flex-direction: column; margin-top: 40px; border-top: 1px solid var(--moxi-border); }
    .row { display: grid; grid-template-columns: 96px 1fr auto; align-items: baseline; width: 100%;
           padding: 12px 2px; border: 0; border-bottom: 1px solid var(--moxi-border); background: none;
           color: var(--moxi-text); font: inherit; font-size: 14px; text-align: left; text-decoration: none; }
    a.row, button.row { cursor: pointer; }
    .label, .time { color: var(--moxi-text-dim); }
    .time { font-variant-numeric: tabular-nums; }
    .fact { min-width: 0; padding-right: 18px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .heavy { font-weight: 600; }
    .entry.open .row { border-bottom: 0; padding-bottom: 5px; }
    .seat { padding: 0 2px 7px 98px; border-bottom: 1px solid var(--moxi-border); }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: -2px; }
    [hidden] { display: none; }
    @media (min-width: ${unsafeCSS(SIDE_FROM_PX)}px) {
      :host { margin-left: var(${unsafeCSS(SIDE_MARGIN)}, auto); }
    }
    @media (max-width: 720px) {
      :host { padding-top: 0; }
      .spot { margin-top: 10px; }
      .rows { margin-top: 10px; }
      .rows.phone { margin-top: 0; }
      .row { grid-template-columns: 1fr auto; row-gap: 2px; }
      .fact { grid-row: 2; grid-column: 1 / 3; padding-right: 0; white-space: normal; overflow-wrap: anywhere; }
      .fact:empty { display: none; }
      .time { grid-row: 1; grid-column: 2; }
      .seat { padding-left: 2px; }
    }
  `;

  static properties = {
    modules: { state: true },
    rows: { state: true },
    main: { state: true },
    loading: { state: true },
  };

  modules: ModuleView[] | null = null;
  rows: Row[] = [];
  /** The main piece shown: its module and name, or null while there is none. */
  main: string | null = null;
  /** True while a main piece is announced and has neither been placed nor failed. */
  loading = false;
  #spot = document.createElement("div");
  #wanted = "";
  #placing = 0;
  #stop: (() => void) | null = null;
  /** The piece in the spot whose side panel the column stands beside. */
  #sider: Node | null = null;

  constructor() {
    super();
    this.#spot.addEventListener(SIDE, (event) => {
      event.stopPropagation();
      this.#side(event.target as Node, readSide((event as CustomEvent).detail));
    });
    // A piece that leaves the spot takes its panel with it.
    new MutationObserver(() => {
      if (this.#sider !== null && !this.#spot.contains(this.#sider)) this.#side(null, 0);
    }).observe(this.#spot, { childList: true, subtree: true });
  }

  connectedCallback(): void {
    super.connectedCallback();
    this.#stop = subscribe("ui", "modules", (data) => {
      this.modules = Array.isArray(data?.modules) ? data.modules : [];
    });
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.#stop?.();
    this.#stop = null;
  }

  protected willUpdate(changed: PropertyValues): void {
    if (!changed.has("modules") || this.modules === null) return;
    this.#seatRows(homeRows(this.modules).filter((row) => installed(row.module)));
    if (!gateway()) void this.#seatMain(mainPiece(this.modules.filter((mod) => installed(mod.name))));
  }

  /** Puts the shown rows in their order on the page. The entries stay where they are in
   * the document, so no component is taken out and placed again when a row rises; the
   * page's policy allows no style attribute, so the order is set on the style object. */
  protected updated(): void {
    const place = new Map(shownRows(this.rows).map((row, at) => [row.key, at]));
    for (const entry of this.renderRoot.querySelectorAll<HTMLElement>(".entry")) {
      entry.style.order = String(place.get(entry.dataset.row ?? "") ?? 0);
    }
  }

  /** Keeps a row for every row component the modules list names, each placed once. */
  #seatRows(seats: HomeSeat[]): void {
    const kept = new Map(this.rows.map((row) => [row.key, row]));
    this.rows = seats.map((item) => {
      const key = `${item.module}.${item.name}`;
      const url = scriptOf(item);
      const old = kept.get(key);
      if (old !== undefined && old.url === url) return Object.assign(old, item);
      const host = document.createElement("div");
      host.className = "seat";
      host.hidden = true;
      const row: Row = { ...item, key, url, host, report: null, clicked: false };
      host.addEventListener(REPORT, (event) => {
        event.stopPropagation();
        this.#heard(row, (event as CustomEvent).detail);
      });
      void seat(host, item, () => this.rows.includes(row));
      return row;
    });
  }

  #heard(row: Row, detail: unknown): void {
    row.report = readReport(detail);
    // A component that holds itself open, or no longer opens, is closed once it lets go.
    if (row.report === null || !row.report.opens || row.report.open) row.clicked = false;
    this.#show(row);
    this.requestUpdate();
  }

  /** Shows or hides a row's component and tells it so with the attribute `open`. */
  #show(row: Row): void {
    const open = rowOpen(row.report, row.clicked);
    row.host.hidden = !open;
    row.host.firstElementChild?.toggleAttribute("open", open);
  }

  #toggle(row: Row): void {
    row.clicked = !row.clicked;
    this.#show(row);
    this.requestUpdate();
  }

  /** Moves the column beside a side panel `width` px wide, or back with 0. The page's
   * policy allows no style attribute, so the margin is set on the style object. */
  #side(from: Node | null, width: number): void {
    this.#sider = width > 0 ? from : null;
    if (width > 0) this.style.setProperty(SIDE_MARGIN, sideMargin(width));
    else this.style.removeProperty(SIDE_MARGIN);
  }

  async #seatMain(piece: HomeSeat | null): Promise<void> {
    const wanted = piece === null ? "" : `${piece.module}.${piece.name} ${scriptOf(piece)}`;
    if (wanted === this.#wanted) return;
    this.#wanted = wanted;
    const mine = (this.#placing += 1);
    this.#spot.replaceChildren();
    this.main = null;
    this.loading = piece !== null;
    if (piece === null) return;
    const placed = await seat(this.#spot, piece, () => mine === this.#placing);
    if (mine !== this.#placing) return;
    this.loading = false;
    if (placed) this.main = wanted;
  }

  render() {
    if (this.modules === null) return nothing;
    const shown = shownRows(this.rows);
    const phone = gateway();
    return html`
      ${phone ? nothing : html`<section class="spot">${this.#spot}${this.main === null && !this.loading && !anyNeeds(this.rows)
        ? html`<p class="quiet">Nothing needs you.</p>` : nothing}</section>`}
      <nav class=${phone ? "rows phone" : "rows"} aria-label="Now" ?hidden=${shown.length === 0}>
        ${repeat(this.rows, (row) => row.key, (row) => html`<div
          class=${rowOpen(row.report, row.clicked) ? "entry open" : "entry"} data-row=${row.key}
          ?hidden=${row.report === null}>
          ${row.report === null ? nothing : this.#line(row, row.report)}${row.host}</div>`)}
      </nav>`;
  }

  #line(row: Row, report: RowReport) {
    const mode = rowMode(report);
    const open = rowOpen(report, row.clicked);
    const cells = html`<span class="label">${row.title}</span><span class="fact">${report.parts.map((part) =>
      (part.heavy ? html`<span class="heavy">${part.text}</span>` : part.text))}</span><span class="time">${
      mode === "toggle" && open ? "Hide" : report.time}</span>`;
    if (mode === "toggle") {
      return html`<button type="button" class="row" aria-expanded=${open ? "true" : "false"}
        @click=${() => this.#toggle(row)}>${cells}</button>`;
    }
    // The shell's own rows lead to the modules view: its page at "" is this one.
    const route = row.module === "ui" ? "modules" : "";
    if (mode === "held" || !components(row.module).some((comp) => comp.name === "page")) {
      return html`<div class="row">${cells}</div>`;
    }
    return html`<a class="row" href=${`/m/${row.module}/${route}`} @click=${(event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(row.module, route);
    }}>${cells}</a>`;
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, UiHome);
