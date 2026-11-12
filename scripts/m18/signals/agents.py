"""The agent sessions on this PC, one item each. value = the share that needs the user."""
import json
import time
from pathlib import Path

ABOUT = "agent sessions (Claude Code, Codex): items = one row per session; value = share waiting on you"
LABEL = "AGENTS"
PERIOD = 2


def _out(rows):
    live = [r for r in rows if r.get("state") in ("working", "waiting", "finished")]
    need = sum(1 for r in live if r.get("state") == "waiting" or r.get("crashed"))
    text = f"{len(live)} agents" + (f", {need} waiting" if need else "")
    return {"value": need / len(live) if live else 0.0, "text": text, "items": rows}


def read(hub):
    """Rows {id, title, label, state, kind, silent, agent, species, tool, subagents, commits, crashed, grid, path}.
    Roster from plugins/sessions.py (or its state/sessions-summary.json), facts from the transcripts."""
    try:
        scan, face_for = hub.fn("sessions.scan"), hub.fn("sessions.face_for")
        ss, rows = scan(), []
        for s in ss:
            if s.get("on_pad"):
                label, grid = face_for(s, ss)
                rows.append(dict(id=s["id"], title=s.get("title"), state=s.get("state"), kind=s.get("wkind"),
                                 silent=bool(s.get("silent")), silent_s=s.get("silent_s"), urgency=s.get("urgency"),
                                 label=label, grid=grid, agent=s.get("kind"), path=s.get("path")))
    except Exception:                                    # noqa: BLE001  no sessions plugin here: its summary file
        state = Path(hub.base or ".") / "state"
        if (state / "sessions-summary.json").exists():   # unreadable (caught mid-write) raises: the last roster stands
            json.loads((state / "sessions-summary.json").read_text(encoding="utf-8"))
        rows = hub.fn("fishbowl._summary_rows")(state)
    return _out(hub.fn("fishbowl.enrich")(rows, {}, time.time()))


_CAST = [("opal", "claude", "opus", 0), ("sorrel", "claude", "sonnet", 7), ("haze", "claude", "haiku", 13),
         ("luna", "codex", "luna", 19), ("sol", "codex", "sol", 23), ("fern", "claude", "sonnet", 31)]
_TOOLS = ["read", "edit", "run", "web", None]


def sim(t):
    """Six sessions that take turns working, waiting, going quiet and finishing, so every behaviour shows."""
    rows = []
    for i, (name, agent, species, off) in enumerate(_CAST):
        ph = int((t + off) // 14) % 6                    # each stage lasts 14 s
        state, kind, silent = "working", None, False
        if ph == 3:
            state, kind = "waiting", ("question", "approval", "blocked")[i % 3]
        elif ph == 4:
            silent = True
        elif ph == 5:
            state = "finished"
        rows.append(dict(id=f"sim-{name}", title=name, label=name, state=state, kind=kind, silent=silent, agent=agent,
                         species=species, tool=_TOOLS[(int(t // 9) + i) % 5] if state == "working" else None,
                         subagents=2 if (i == 0 and ph in (1, 2)) else 0,
                         commits=[f"c{k}" for k in range(int((t + off) // 40) % 5)], grid=None, path=None))
    return _out(rows)
