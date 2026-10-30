---
name: handoff
description: Write a handoff, the short file the next session of your seat reads first. Use when you are asked to write a handoff or hand off, and when you stop with work left for the next session of the same seat.
---

# Handoff

A handoff is a short file for the next session of the same seat. It says where the work
is and points to everything else by path. When the next session starts, the launcher
shows it a line naming the handoff, and the session reads it before other work.

## What goes in it

- Where the work is, in two or three lines.
- Pointers by path to everything else: designs, notes, reports, branches, worktrees.
- What the next session does first.
- Messages that still owe an answer, either way, by their id.
- Sessions or workers you started, by name.

Leave out what a file already holds, such as decisions, evidence or a queue's contents.
Point to the file instead. Aim for 100 lines at most, and never go past 200.

## Where it goes

Find your handoff folder:

1. The `handoff dir:` line of your launch identity block, if it has one.
2. Else run `moxi launcher handoff dir` in your session's shell. It prints the folder,
   or says your session has no handoff folder.

If neither gives a folder, your session has no key (no comms name, seat or project), and
a handoff written anywhere would not be shown to your next session. Say so and stop.

## The file name

Name the file `<YYYY-MM-DD>-<key>.md`, with today's date. The key is, in this order:

- your comms name, without any `@` part;
- else the name of the seat you were launched as;
- else your launch's project key.

If today's name is taken, add `-2`, then `-3`, and so on, before `.md`. The launcher
shows your next session the newest handoff of your key it hasn't been shown yet, so a new
handoff never overwrites an old one.

`moxi launcher handoffs --key <key>` lists the handoffs of a key and which are waiting.
