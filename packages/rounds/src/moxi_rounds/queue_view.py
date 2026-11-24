"""What `moxi rounds queue` says of a repo's queue, as lines: whether a lander runs, a
pause and its reason, a wait on something outside the queue, each running batch and its
CI reading (two or more while a red batch is split) or the CI run it waits on to end,
each member still in the queue in the order it is taken, the target's last full run
(and, where a batch was read by it, the candidate's ref), the newest landing and its candidate's run, a full run still running at the newest head
read, and the messages the lander could not send.

Everything said is read from the queue file (`queue_file.read`); nothing is written.
"""

from __future__ import annotations

from . import queue_file, telling

#: How many of the messages that reached no name are listed, newest last.
UNDELIVERED_SHOWN = 5


def lines(queue: queue_file.Queue, *, lander_runs: bool,
          started: str | None = None) -> list[str]:
    """The lines for `queue`. `lander_runs` says whether a lander holds the repo's lock,
    and `started` how one was started by this command, None when none was."""
    said = [f"queue of {queue.repo}"]
    if started is not None:
        said.append(f"lander: started ({started}), since a member waits and none ran")
    else:
        said.append("lander: running" if lander_runs else "lander: none running")
    if queue.paused is not None:
        said.append(f"paused: {queue.paused}")
        said.append(f"  fix: moxi rounds queue --repo {queue.repo} --resume, once that is "
                    "dealt with; urgent members still land meanwhile")
    if queue.waiting_on is not None:
        said.append(f"waiting: {queue.waiting_on.get('reason')}")
    for running in queue.running_batches():
        run = running.run or {}
        reading = run.get("reading") or "not read yet"
        url = f"   {run['url']}" if run.get("url") else ""
        read = next((one for one in reversed(running.runs) if one.get("kind") == "read"),
                    None)
        if read is not None and read.get("running"):
            reading = f"still running: {_running(read)}"
        said.append(f"batch {running.id} on {running.main[:12]}: {len(running.members)} "
                    f"member(s), CI {reading}{url}")
    members = queue.queued()
    if not members:
        said.append("members: none waiting")
    for position, member in enumerate(members, start=1):
        where = (f", in batch {member.batch}" if member.state == queue_file.IN_BATCH
                 else "")
        urgent = f", urgent (by {member.by})" if member.urgent else ""
        said.append(f"  {position}. {member.name} {member.stage} at {member.tip[:12]}"
                    f"{urgent}{where}")
    if queue.mains:
        last = queue.mains[-1]
        said.append(f"main's last full run: {last['event']} at "
                    f"{str(last.get('head'))[:12]}, run {last.get('run')}"
                    + candidates_run(queue, last))
    landing = landing_line(queue)
    if landing is not None:
        said.append(landing)
    head_read = next((one for one in reversed(queue.head_runs)
                      if one.get("kind") == "read"), None)
    if (head_read is not None and head_read.get("running")
            and queue.main_at(str(head_read.get("head"))) is None):
        said.append(f"main's full run at {str(head_read.get('head'))[:12]}: still "
                    f"running: {_running(head_read)}")
    spell = spell_line(queue)
    if spell is not None:
        said.append(spell)
    for one in queue.head_runs:
        if one.get("kind") == "gave up" and one.get("ref"):
            said.append(f"main's full run at {str(one.get('head'))[:12]} never started: "
                        f"{one.get('ref')} was pushed twice")
        elif one.get("kind") == "gave up":
            said.append(f"main's full run at {str(one.get('head'))[:12]} never showed, "
                        "and the ci setting names no ref to push it to")
    return said + told_lines(queue)


def candidates_run(queue: queue_file.Queue, main: dict) -> str:
    """Where the `red` or `green` event `main` names a run a batch was read by, the
    words that say so, with the ref the batch was pushed to; else nothing."""
    batch_id = next((queue.candidate_runs[str(one)] for one in main.get("run") or ()
                     if str(one) in queue.candidate_runs), None)
    if batch_id is None:
        return ""
    batch = queue.batch(batch_id)
    pushed = next((one for one in (batch.runs if batch is not None else [])
                   if one.get("kind") == "pushed" and one.get("ref")), None)
    where = (pushed or {}).get("ref") or (batch.branch if batch is not None else batch_id)
    return f", the candidate's run on {where}"


def landing_line(queue: queue_file.Queue) -> str | None:
    """The newest landing and the candidate's run it landed on: its batch's newest
    read that reads green, or, for a landing past the target's own failures, its newest
    read; with that read's workflows and the job each of their jobs stands for. None
    before any landing."""
    landed = next((one for one in reversed(queue.events)
                   if one.get("event") == queue_file.LANDED and one.get("batch")), None)
    if landed is None:
        return None
    batch = queue.batch(str(landed["batch"]))
    reads = [one for one in (batch.runs if batch is not None else [])
             if one.get("kind") == queue_file.READ_KIND]
    read = next((one for one in reversed(reads) if one.get("reading") == "green"),
                reads[-1] if reads else {})
    flows = read.get("workflows") or []
    paths = ", ".join(str(one.get("path")) for one in flows)
    stands = ", ".join(f"{job} stands for {stood}" for one in flows
                       for job, stood in (one.get("stands_for") or {}).items())
    named = "; ".join(part for part in (paths, stands) if part)
    runs = ", ".join(map(str, read.get("runs") or [])) or "unknown"
    return (f"last landing: {landed.get('round')} {landed.get('stage')} at "
            f"{str(landed.get('landing') or '')[:12]}, on its candidate's run {runs}"
            + (f" ({named})" if named else ""))


def _running(read: dict) -> str:
    """The runs a read event found still running, and since when."""
    return f"run {', '.join(map(str, read['running']))}, since {read.get('at')}"


def spell_line(queue: queue_file.Queue) -> str | None:
    """The red spell main is in, as `queue` and `next` say it: since which head and who
    owns the fix, and, when comms read no running session for the owner nor for the
    director, that the notice waits. None when main's last full run read is not red."""
    red = queue.mains[-1] if queue.mains else None
    if red is None or red.get("event") != queue_file.RED:
        return None
    since = str(red.get("since") or red.get("head"))[:12]
    owner = red.get("owner")
    notice = next((one for one in reversed(queue.told)
                   if one.get("about") == "director"
                   and str(one.get("key", "")).startswith(f"red:{red.get('since')}")), None)
    if notice is not None and notice.get("director_state") not in (None, "held"):
        return (f"main red since {since}; {owner or 'no owner'} and {notice.get('to')} "
                "have no running session; the notice waits")
    if notice is not None and notice.get("delivery") == telling.NOBODY:
        return (f"main red since {since}; {owner or 'no owner'} has no running session "
                "and no director is set")
    if owner:
        return f"main red since {since}: {owner} owns the fix"
    return f"main red since {since}: no landing with a lead owns it; the director gives it"


def told_lines(queue: queue_file.Queue) -> list[str]:
    """The messages the lander sent that no name received (`telling.undelivered`): the
    newest `UNDELIVERED_SHOWN`, each with every name tried, what became of each send,
    and a fix by its cause; and a director not set said once. A round with no lead is
    said by `next`, not here."""
    found = telling.undelivered(queue)
    nobody = [one for one in found if one.get("delivery") == telling.NOBODY
              and not one.get("member")]
    messages: dict[tuple, list[dict]] = {}
    for one in found:
        if one.get("delivery") != telling.NOBODY:
            messages.setdefault(telling.message_of(one), []).append(one)
    said = []
    if nobody:
        said.append(f"director: {nobody[-1].get('why')}; {len(nobody)} notice(s) about "
                    "the queue went to nobody")
    if messages:
        said.append(f"messages not delivered: {len(messages)}")
    for sends in list(messages.values())[-UNDELIVERED_SHOWN:]:
        said.append(f"  {sends[0].get('about')}: " + "; ".join(map(_send, sends)))
        said += [f"    fix: {fix}" for fix in dict.fromkeys(
            fix for fix in map(_fix, sends) if fix is not None)]
    return said


def _send(record: dict) -> str:
    """One send of an undelivered message: the name, what became of it and why."""
    state = ("tried again each pass" if record.get("delivery") == telling.OFFLINE
             else record.get("delivery"))
    why = f" ({record['why']})" if record.get("why") else ""
    return f"{record.get('to')}: {state}{why}"


def _fix(record: dict) -> str | None:
    """What fixes the cause of an undelivered send, None where nothing is to be done or
    nothing is known."""
    why = str(record.get("why") or "")
    member = record.get("member") or []
    if why.startswith("credential"):
        return "the lander holds no comms token: moxi doctor comms"
    if why.startswith("never-bound") and member:
        project = str(member[0]).split("/", 1)[0]
        return (f"name a lead who can receive: moxi rounds mark {member[0]} --lead NAME, "
                f"or moxi projects set {project} --lead NAME")
    return None


__all__ = ["UNDELIVERED_SHOWN", "lines", "candidates_run", "landing_line", "spell_line",
           "told_lines"]
