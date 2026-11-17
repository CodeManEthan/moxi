// <moxi-peer-row>: peer's row on Home. It reports `quiet`, needing nothing, each time it is
// connected.

const TAG = "moxi-peer-row";

if (!customElements.get(TAG)) {
  customElements.define(TAG, class extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot === null) this.attachShadow({ mode: "open" });
      this.dispatchEvent(new CustomEvent("moxi-home-row", {
        detail: { parts: [{ text: "quiet" }], needs: 0 }, bubbles: true, composed: true,
      }));
    }
  });
}
