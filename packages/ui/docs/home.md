# A module on Home

Home is the shell's first page, at `/m/ui/`. It has one main spot and, under it, a list of
rows: one line per module that has something to say right now. A module gets onto Home by
announcing a component for it; the shell names no module.

## Announcing a seat

A component table in the module's `COMPONENTS` takes a seat on Home with three fields:

```python
COMPONENTS = {
    "now": {"script": "now.js", "title": "Sessions", "doc": "What is running now",
            "contract": 1, "place": "block", "home": True,
            "home_as": "row", "order": 20},
}
```

- `home_as: "row"` is one row under the spot. `title` is the row's label and `order` its
  fixed place among the rows, lower first.
- `home_as: "main"` is a piece for the main spot. Home has one spot, and shows the main
  piece with the lowest `order` among the installed modules (ties go to the module whose
  name sorts first).
- Without `home_as`, a block with `home: True` is shown on the modules view, as before.

`home_as` needs `home: True` and `place: "block"`. With anything else, or with another
word, the component is refused and the modules view says why. `order` is a whole number
from 0 to 999; a missing or wrong one reads as 500.

The `order` scale for a main piece: under 100 for a piece that is the reason most people
open Moxi. A row's `order` is only its place among the rows; the shell's own Health row is
at 900, so a row with a lower number sits above it.

With no main piece installed the spot says `Nothing needs you.`, or nothing while a row
needs the user. The phone gets no spot: its Home is the rows.

## The row contract

Home places a row component in a host it keeps hidden, as soon as the modules list names
it, and draws the row itself, so every row looks the same. The component says what its
line is by dispatching an event from itself whenever the line changes:

```js
this.dispatchEvent(new CustomEvent("moxi-home-row", {
  bubbles: true, composed: true,
  detail: {
    parts: [{ text: "3 running", heavy: true }, { text: " · planner goes cold at 19:22" }],
    time: "read 19:08",
    needs: 0,
    opens: true,
  },
}));
```

| field | what it means |
|---|---|
| `parts` | The fact, as pieces of text written one after the other. Home adds nothing between them, so a part brings its own ` · ` or `, `. A part with `heavy: true` is set at weight 600. |
| `time` | Optional. The dim text at the right end, such as `read 19:08`. |
| `needs` | Optional. Above 0, the row needs the user; see the scale below. Absent or 0, it does not. |
| `opens` | Optional. `true`: a click on the row opens the component in place. |
| `open` | Optional, with `opens`: the row is shown opened with no click. |

What Home does with it:

- **Nothing to report.** A `detail` of `null`, or no event yet, shows no row. Report
  `null` when there is nothing worth a line; a module with nothing to say is not on Home.
- **The line.** The component's `title` in a 96 px column, the parts, then the time. It
  is one line, cut with an ellipsis. At 720 px and under the label and the time share the
  first line and the fact wraps under them.
- **Order.** Rows that need the user sit above every row that does not, the lower `needs`
  first, then by `order`. The rest keep their fixed places by `order`. No row changes
  colour; say what is wrong in words and use `heavy` for the part that matters.
- **Opening in place.** With `opens`, a click sets the attribute `open` on the component
  and shows it under the row, at the fact's column, pushing the rows below down. `Hide`
  takes the time's place and a second click closes it, removing the attribute. Draw the
  opened content in the component's own shadow root when `open` is set. The component may
  go on reporting while it is open.
- **Without `opens`**, the row is a link to the module's page.
- **Open with no click.** While a report carries both `opens` and `open`, Home keeps the
  component shown and the attribute set, the row is no toggle and there is no `Hide`. A
  later report without `open` closes it and puts it back under the click rule. The
  component decides when: `gateway()` in `client.js` tells the phone from the desk.
- **Late reports.** Home listens for as long as it is open and has no cut-off, so a row
  shows from its first report however late that comes.
- **A script that does not load** shows nothing on Home. The modules view still lists the
  component.

Declare `open` among the component's `attributes` (`{"open": {"type": "bool"}}`) when it
opens.

### The `needs` scale

| `needs` | when |
|---|---|
| 10 | someone is blocked on the user |
| 20 | something waits for the user |
| 30 | something is broken |
| 40 | something will run out |

Use the number that fits and no other; a row that reports `needs` for something that can
wait pushes down the rows that cannot.

### On the phone

Home on the phone is the rows. The shell says on every phone page, first under the top
line, `This phone reads only here.` A row's
component runs on the phone too, so every route and topic it reads must be one the
gateway serves, with paths and free text marked. Leave out an act the phone cannot reach.

## The Health row

The shell's own row. It reads the doctor run the shell keeps (`GET /m/ui/api/checks`, the
topic `ui.checks`) and says `All checks pass`, how many checks warn, or how many fail; a
failing check gives it `needs: 30`. Opened, it lists only the checks that fail or warn,
each with its fix to copy, the way to the full list on the modules view, and `Check
again`. On the phone it shows the counts and each check's module and name, and no text
of the check's own.
