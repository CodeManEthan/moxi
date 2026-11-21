// <moxi-rounds-page route="...">: rounds' page. This first cut only defines the element,
// so the shell can place it; the board is drawn from `/api/rounds` once the page's
// sources land.

import { LitElement } from "lit";

export class RoundsPage extends LitElement {
  static properties = { route: { type: String } };

  route = "";
}

if (!customElements.get("moxi-rounds-page")) customElements.define("moxi-rounds-page", RoundsPage);
