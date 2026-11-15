---
name: moxi-rounds
description: The judgment a round in moxi rounds leaves to you. When a design needs another pass, which model tier does which step, whether a decision is major, how to cut a build into slices, how to write a ruling or a question that reads cold, and what a re-check reads. Use when you lead or check work in a round. Commands come from moxi rounds next, its refusals and --help, not from here.
---

# Rounds

rounds checks the steps, the gates and the records. This skill holds what a tool can't
check. For commands, run `moxi rounds next`: it prints the step, who does it, what they
read and the command that completes it. Every refusal names its fix, and every verb has
`--help`.

## The brake on design passes

A design gets one full pass; every pass after it re-checks what changed. A second full
pass mostly re-reads text the first cleared, and finds things there only because a fresh
reader always does. rounds records a second full pass as an extra and counts it.

Publishable is final for the text it read. A later pass on that text that wants more is
recorded, and does not reopen the design; otherwise a design is checked until a checker
runs out of things to say.

A fold that changed a mechanism needs a re-check. A mechanism is anything a command
reads, decides, writes, refuses or accepts. A fold that only writes down what a pass
found, or fixes wording, changes none: fold it as records only, pointing to where you
wrote why, and no pass is owed. When you can't tell whether a change is a mechanism, it
is one.

Two `fold-first` verdicts in a row mean the section is wrong at its root, and folding
more findings into it gives the next pass more to find. Stop. Write the rule the section
must follow in a sentence or two, rewrite the section from it and fold. Then close the
thread, or accept its risk, pointing to where you wrote why.

## Which tier does what

A flow names a tier for each step, and the `models` setting maps each tier to a model.

- Use the strongest tier where the work is open-ended and a miss is costly: writing the
  design, building, and the one full pass of each verification.
- A re-check reads a bounded diff against a stated rule, so a lighter tier does it well.
  So does the drift re-check at finish, and the retrospective, which reads numbers and
  reports.

Write the model that actually ran in a report's `model`. A report below its step's tier
is recorded with a flag, not refused, and the flag is counted in the round's numbers.

## Is a decision major?

A decision is a choice the build made that the operator would notice: what he gets
differs from what he ruled or what the design said. The checker weighs it.

- **Major**: it changes what a user sees or types, what a command accepts, refuses or
  writes, a stored format, a default, or what one of his rulings plainly means. He would
  want to rule on it, or at least to know it shipped.
- **Minor**: the behaviour he ruled is intact and only the way there differs. An
  internal name, a function split in two, checks run in another order with the same
  outcome.
- **Forecloses**: it rules something out later, such as a format other tools will read,
  or an option taken away. It is always major, and shown on its own.

When unsure, call it major. A later report can make a decision heavier and can never make
it lighter, so an under-call is the mistake that stays.

## Cutting slices

A slice is one worker's piece of a build: one mechanism or one group of files, well under
an hour. The project's `slice_minutes` is the limit.

- Its `what` names one thing. A `what` that needs "and" between two mechanisms is two
  slices.
- A slice reads only the slices above it. Order them so each one's tests run when it
  lands.
- Put the reader beside the writer. A slice that adds a kind of record also adds it to
  every place that lists records, so nothing stays invisible until a later slice.
- Guess the minutes honestly. A slice that runs long leaves a worker stopping halfway
  through a mechanism.

Size each brief with rounds before you hand it out.

## Writing a ruling

A ruling is read cold, by an agent that never saw the conversation. Write one plain
sentence, or a few, saying what is built or not built.

- Name the thing itself. Not "option b" or "what we said about the cache", but "Search
  reads live notes only; archived notes are left out."
- State the rule, not its history: no "as discussed", no review ids, no account of how
  he got there.
- One rule per ruling, so each can be corrected alone.
- Add a short reason when the rule looks arbitrary without one.
- Keep his meaning. The pointer is where he can check your wording.

## Writing a question

A question is one sentence the operator can answer as it stands, read aloud or on a
phone.

- Ask the choice, not the topic: "Should search read archived notes too?", not "Thoughts
  on archived notes?"
- Give two to four options when the answers are known, each line standing on its own. A
  wrong option is retired and replaced under a new key, never edited.
- Say what it holds. Leaving `blocks` out holds every stage, which is the safe default.
  Name stages only when you know the others don't depend on the answer.
- One fork per question. A question that bundles two gets one answer for both.

## What a re-check reads

A re-check reads what changed and what depends on it, not the whole again.

- A design re-check reads the diff from the last verified version, and the sections
  that use what changed. `next` names the two frozen files.
- A build re-check reads the commits since the head the last verdict read, and the code
  that calls what they changed. `next` prints the range.
- A drift re-check reads the target's change to the paths the build also touched, and
  the build's diff for those paths.

Restate every list in full: each finding still open on the build, with its id, in its
state now. A new finding takes a new id. A re-check that grows into a full pass is the
extra pass the brake counts.
