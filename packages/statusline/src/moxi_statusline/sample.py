"""The sample input the preview and the doctor render: Claude Code's status line input,
with made-up values, in the folder the caller is in.
"""

import os
import time

#: The made-up session the sample names. No module has a record of it, so a segment that
#: reads one (cache's) shows nothing for it.
SESSION = "sample-0000-statusline"


def payload(cwd: str | None = None, now: float | None = None) -> dict:
    """The sample input, with `cwd` (default: this process's working folder) as both its
    `cwd` and `workspace.current_dir`, and limits that reset within the hour."""
    cwd = os.getcwd() if cwd is None else cwd
    now = time.time() if now is None else now
    return {
        "session_id": SESSION,
        "transcript_path": f"/sample/projects/{SESSION}.jsonl",
        "cwd": cwd,
        "model": {"id": "claude-sample", "display_name": "Claude Sample"},
        "workspace": {"current_dir": cwd, "project_dir": cwd},
        "effort": {"level": "high"},
        "context_window": {"used_percentage": 42},
        "cost": {"total_cost_usd": 1.24, "total_lines_added": 12,
                 "total_lines_removed": 3},
        "rate_limits": {
            "five_hour": {"used_percentage": 23, "resets_at": int(now) + 3600},
            "seven_day": {"used_percentage": 61, "resets_at": int(now) + 86400},
        },
    }
