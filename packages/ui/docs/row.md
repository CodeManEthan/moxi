# The two-line row

`<moxi-ui-row>` is the row a list page draws: a name with a fact at the right end, and
under it one line of what was last said, with the row's acts at the end of either line. `/m/ui/client.js` defines it,
so a page places it without importing or bundling anything. It draws what its attributes
carry and reads nothing itself: no route, no module, no rule for what a name is.

```html
<moxi-ui-row toggles
    name="fix-tooltips" where="proj-a" fact="warm until 19:52"
    lead="2 subagents" time="19:09" said-by="you"
    rest="Run the suite and report every diff.">
  <a slot="acts" href="/m/sessions/claude/abc">Open</a>
  <div>Shown under the lines while the row carries open.</div>
</moxi-ui-row>
```

draws

```text
fix-tooltips  proj-a                                    warm until 19:52
2 subagents · 19:09 You: Run the suite and report every diff.       Open
```

## Attributes

A piece whose attribute is unset, empty or blank is not drawn: no gap, no dot, no
placeholder. A line with no piece and no act is not drawn either, so a row with a name
alone is one line, and a row with nothing set and nothing slotted takes no room.

| attribute | line | draws |
|---|---|---|
| `name` | one | The name, in the text colour. |
| `where` | one | Dim text after the name. |
| `fact` | one | Dim text at the line's right end. |
| `fact-heavy` | one | Flag. The fact is in the text colour at weight 600. |
| `lead` | two | The phrase that leads the line, dim. |
| `lead-heavy` | two | Flag. The lead is in the text colour at weight 600. |
| `lead-more` | two | A second leading phrase, dim. |
| `time` | two | A time, dim, in tabular figures, before the rest. |
| `said-by` | two | Set to `you`, `You:` is written before the rest. Any other value draws nothing, and so does `you` on a row with no `rest`. |
| `rest` | two | The rest of the line, dim. |
| `rest-strong` | two | Flag. The rest is in the text colour. |
| `tail` | two | Dim text after the rest. |
| `note` | three | A dim third line. |
| `open` | | Flag. The default slot's content shows under the lines. |
| `toggles` | | Flag. The row's lines can be clicked and line one takes the focus; see below. |

A flag is on when the attribute is present, whatever its value. Text is drawn as text: a
`<` in a value is a `<` on the page.

Line two's phrases are, in order, `lead`, `lead-more`, `time` with `rest`, and `tail`. The
row writes a middle dot between each two that are set. The time, `You:` and the rest are
one phrase, parted by spaces and no dot.

## Slots

- `acts-one`: the right end of line one, after `fact`, for an act that belongs on the
  row's first line, such as an `Open` on a row with nothing to say on line two. It takes
  line one's size and the dim colour unless the page styles it. It sits beside line one's
  pieces, not inside them, so it is never part of the row's button, and it is never cut:
  a narrow line one cuts its pieces first. With an act here and no piece, line one holds
  the act alone, at its right end. A row whose only act is in `acts-one` is one line:

  ```html
  <moxi-ui-row toggles name="alpha" where="~/code/alpha" fact="last active 19:09">
    <button slot="acts-one" type="button">Open</button>
  </moxi-ui-row>
  ```

  ```text
  alpha  ~/code/alpha                          last active 19:09   Open
  ```

- `acts`: the right end of line two, for the row's links and buttons. They take line two's
  size and dim colour unless the page styles them. With acts and no phrase, line two holds
  the acts alone. A page that leaves acts out on the phone does not slot them there.
- The default slot: the opened content. It is hidden until the row carries `open`.

## Opening a row

Only a row with `toggles` can be opened by its lines. Such a row shows a pointer over its
lines, and a click anywhere on them sends `moxi-row-toggle`. The event bubbles, crosses
shadow roots and carries no detail; its target is the row. A click or a key on something
in `acts-one` or `acts`, or in the opened content, is not the row's and sends nothing. A
page whose act opens the row sets `open` itself.

With the keyboard, line one's pieces are the row's tab stop, with a focus ring. They are a
button (`role="button"`) whose `aria-expanded` follows `open`, so a screen reader says the
name, where and fact and whether the row is open. The acts of both slots are outside the
button and stay links and buttons of their own. Enter or Space on the stop sends
`moxi-row-toggle`. The row takes Space from the page, so the page does not scroll. A row
with `toggles` and no piece on line one keeps its lines as the tab stop, with no role and
no `aria-expanded`, whether or not `acts-one` holds an act.

A click that ends a selection of text does not toggle: drag over a name to copy it and
the row stays as it is. The next plain click clears the selection and toggles. Enter and
Space toggle whatever is selected.

The row does not open itself. The page hears the event and sets or removes `open`:

```js
list.addEventListener("moxi-row-toggle", (event) => {
  event.target.toggleAttribute("open");
});
```

A row without `toggles` sends nothing, takes no focus and has no role and no
`aria-expanded`, with `open` set or not.

## Width

Each line is one line. When line one is too narrow, `where` is cut first, with an
ellipsis, then `name`; `fact` is cut only when it alone is wider than the room the
`acts-one` act leaves. That act is kept whole and on line one at any width. Line two cuts
its phrases with one ellipsis and keeps the acts whole. At 720 px and under, line two
wraps instead.

## Looks

The row is 14 px text with 7 px above and below; lines two and three are 13 px. The
`acts-one` act stands 14 px after the fact, 10 px at 720 px and under. It takes
its colours from the theme's tokens (`--moxi-text`, `--moxi-text-dim`, and
`--moxi-accent` for the focus ring) and has no border of its own: the list that holds
the rows draws any line between them. An opened row has 10 px under its content.
