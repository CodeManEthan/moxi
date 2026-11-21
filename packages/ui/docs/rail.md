# The rail

`<moxi-ui-rail>` is a line of step markers with a line through what is done: each step a
marker of its own shape, a short word under it, and the line solid up to where the work
is and dashed after. `/m/ui/client.js` defines it, with its cells `<moxi-ui-mark>`, so a
page places it without importing or bundling anything. It draws what its marks' attributes
carry and reads nothing itself: no route, no module, no rule for what a step is.

```html
<moxi-ui-rail>
  <moxi-ui-mark state="done" word="v8" settled key="design" label="design: done, v8"></moxi-ui-mark>
  <moxi-ui-mark state="done" word="pub" settled key="verify-design" label="verify: done, publishable"></moxi-ui-mark>
  <moxi-ui-mark state="yours" count="3" here key="docket" label="docket: 3 waiting on you"></moxi-ui-mark>
  <moxi-ui-mark wide><span>B1</span> billing-export <button type="button">+1</button></moxi-ui-mark>
  <moxi-ui-mark state="waiting" word="held" key="commission|B1" label="commission of B1: held"></moxi-ui-mark>
  <moxi-ui-mark></moxi-ui-mark>
</moxi-ui-rail>
```

draws

```text
                    ▼
  ●━━━━━━●━━━━━━◆³ B1 billing-export [+1] ┄┄◌
  v8     pub                                held
```

## The marks

Each `<moxi-ui-mark>` child of the rail is one cell, in order. Any other child is not
drawn.

| attribute | draws |
|---|---|
| `state` | The marker's shape, one of the eight below. Unset, blank or any other word, the cell draws no marker and the line passes through it. |
| `word` | A short word under the marker, 11 px, dim; in the done colour for `done`, the failed colour for `failed`, and the waiting colour at weight 600 for `yours`. |
| `count` | A number at the marker's upper right, 11 px at weight 700: in the waiting colour on a `yours` marker, dim on any other. |
| `label` | The marker's accessible name, used as given. Unset or blank, the name is the state in words, then the count and the word, each when set: `waiting on you, 3`. |
| `key` | Nothing. It is opaque, and comes back in the event. |
| `settled` | Flag. The step is settled, for the line (below). |
| `here` | Flag. `▼` over the marker: the step the line is at. A line can have more than one. |
| `open` | Flag. The page sets it while whatever the marker opened shows: the marker takes the accent's ring on the accent's soft fill, and its `aria-expanded` is `true`. |
| `quiet` | Flag. The marker is drawn and named, as an image, but is no button and sends nothing. |
| `wide` | Flag. The cell takes the label width and shows the mark's own children over the line, with no marker, whatever its `state`. |

A flag is on when the attribute is present, whatever its value. Text is drawn as text: a
`<` in a value is a `<` on the page.

| state | shape | in words |
|---|---|---|
| `done` | filled circle, green | done |
| `none` | short dash | done, nothing owed |
| `yours` | diamond, amber | waiting on you |
| `failed` | circle with a cross, red | failed or sent back |
| `owed` | ring with a play mark | owed by an agent |
| `waiting` | dashed ring | waiting on something else first |
| `todo` | small hollow ring | not reached yet |
| `child` | rounded square with a dot | built in a round of its own |

Each state has its own shape, so a rail reads in greyscale. Only `done`, `yours` and
`failed` take a colour; the other five are drawn in the text, dim and border colours.

## The line

The line runs from the first cell with a marker to the last; nothing is drawn before the
first or after the last. It is solid, 3 px in the done colour, from the first marker
through each `settled` marker up to and including the first marker not settled, and
dashed, 1 px in `--moxi-border-strong`, after it. A settled marker after the first one
not settled does not make the line solid again. A cell with no marker, a wide one
included, takes the stretch it stands in, so two rails whose cells line up draw their
lines in step.

`settled` is the page's to set: the rail has no rule for which states are settled.

## Pressing a marker

A marker is a button, except on a quiet mark, a wide mark and every mark of a small rail.
A click on it, or Enter or Space while it holds the focus, sends `moxi-rail-mark`. The
event's target is the rail; it bubbles, crosses shadow roots, and carries `{key}`, the
mark's `key` or `null` when it has none. Enter and Space send once a press: a key held
down sends nothing more, and a key held with Shift, Ctrl, Alt or Meta sends nothing. Tab
moves from marker to marker, in order, with a focus ring in the accent. `mark.focus()`
focuses the mark's marker, so a page that closes what a marker opened can give it the
focus back.

The rail opens nothing itself. The page hears the event and sets `open` on the mark while
it shows the step:

```js
rail.addEventListener("moxi-rail-mark", (event) => {
  const mark = [...event.target.children].find((node) => node.getAttribute("key") === event.detail.key);
  mark?.toggleAttribute("open");
});
```

A click on a page's own button in a wide mark is the page's: the rail sends nothing for it.

## Widths

A cell is `--moxi-rail-cell` wide (56 px) and a wide cell `--moxi-rail-label` (132 px).
Set either on the rail, or on a box that holds rails, and every rail set alike lines its
cells up with the others. Each cell is 34 px high.

Where a wide mark's children stand, they hide the line under them with
`--moxi-rail-bg`, which is `--moxi-bg` unless the page sets it: a page whose line takes a
hover or selected background sets `--moxi-rail-bg` to the same colour there.

## A small rail

`size="small"` draws a picture, for a card or a list: 11 px markers with a 7 px stretch of
line between each two (2 px solid, 1 px dashed), no words, no counts, no `▼`, and no
buttons. Each marker is an image named as above. The rail is as wide as its marks and
sits inline.

## A vertical rail

`vertical` stacks the marks, one a row, for a narrow screen: the marker at the left, its
`label` beside it with `◂ here` after a `here` mark's label, and its `word` under the
label. The line runs down from each marker to the next, solid and dashed by the same rule.
A count and `▼` are not drawn. Markers stay buttons unless the mark is quiet; the text
beside them is for the eye, since the marker's name already says it. A vertical rail is
never small.

## Looks

The state colours are three custom properties at the top of the rail's stylesheet,
`--moxi-rail-done`, `--moxi-rail-yours` and `--moxi-rail-failed`, set from the theme's
`--moxi-state-done`, `--moxi-state-yours` and `--moxi-state-failed`. Those tokens are a
green, an amber and a red in every colour set, `plain` included, and nothing in the rail
reaches a state colour through `--moxi-ok`, `--moxi-attention` or `--moxi-error`, which
are the text colour in `plain`. The theme also has `--moxi-state-yours-soft`, an amber
tint, for the background of what waits on the user. Every other colour is the theme's ink
and greys: `--moxi-text`, `--moxi-text-dim`, `--moxi-border-strong`, `--moxi-bg` and
`--moxi-bg-raised`, with `--moxi-accent` and `--moxi-accent-soft` for an open marker and
the focus, and `--moxi-bg-sunken` under the pointer.
