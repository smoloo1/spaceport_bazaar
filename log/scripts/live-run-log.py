"""Live dashboard and report for a Bazaar client's JSONL journal.

In a terminal this opens an interactive dashboard that follows the game while
run_live.py writes it. Piped output, or --plain, prints a text report instead.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time


RESOURCES = ("water", "food", "components")
COMMANDS = ("advertise", "offer", "accept", "withdraw", "ready", "sync")
LOG_DIR = Path(__file__).resolve().parents[1] / "run" / "logs"
# Server codes in plain language. Unknown codes fall back to a tidied name.
FRIENDLY = {
    "RESULT_CODE_REQUEST_ID_CONFLICT": "request ID already used",
    "RESULT_CODE_RUN_NOT_RUNNING": "game not running",
    "RESULT_CODE_RATE_LIMITED": "rate limited",
    "RESULT_CODE_INVALID_ARGUMENT": "invalid command",
    "RESULT_CODE_NOT_FOUND": "not found",
    "RESULT_CODE_EXPIRED": "already expired",
    "RESULT_CODE_NOT_OPEN": "no longer open",
    "RESULT_CODE_LIMIT_REACHED": "limit reached",
    "RESULT_CODE_INSUFFICIENT_RESOURCES": "not enough resources",
    "RESULT_CODE_STATION_FAILED": "planet has failed",
    "CONTROL_CODE_BAD_MESSAGE": "malformed message",
    "CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED": "too many requests",
    "CONTROL_CODE_UNSUPPORTED_VERSION": "unsupported protocol version",
    "CONTROL_CODE_RUN_MISMATCH": "wrong game",
    "CONTROL_CODE_INVALID_AUTHENTICATION": "token rejected",
    "CONTROL_CODE_SESSION_FENCED": "another client took over this planet",
}


def num(value):
    """Protobuf JSON writes uint64 fields as strings ("0" is truthy), so convert before comparing."""
    return int(value or 0)


def nullable(value):
    """Read protobuf JSON nullable wrappers; return None for a null wrapper."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def resource_name(value):
    return str(value).removeprefix("RESOURCE_").lower()


def friendly(code):
    code = str(code)
    return FRIENDLY.get(code, code.removeprefix("RESULT_CODE_").removeprefix("CONTROL_CODE_").replace("_", " ").lower())


def amounts(value):
    return tuple(num((value or {}).get(r)) for r in RESOURCES)


def parse_time(stamp):
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- journal loading

class Source:
    """Reads journals incrementally. Without an explicit path, follows the newest game in
    run/logs and stitches together every journal from that game (reconnects write new files)."""

    def __init__(self, path=None):
        self.path = path
        self.files = {}  # path -> {"offset", "entries", "run_id", "partial"}
        self.included = []

    def _read(self, path):
        info = self.files.setdefault(path, {"offset": 0, "entries": [], "run_id": None, "partial": b""})
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size <= info["offset"]:
            return False
        with path.open("rb") as f:
            f.seek(info["offset"])
            data = info["partial"] + f.read()
        info["offset"] = size
        # Keep an unfinished last line until the client writes the rest of it.
        *lines, info["partial"] = data.split(b"\n")
        for line in lines:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            info["entries"].append(entry)
            if info["run_id"] is None and entry.get("event") == "message" and "state" in entry.get("message", {}):
                info["run_id"] = entry["message"]["state"].get("run_id")
        return True

    def refresh(self):
        """Read new data; return True when anything changed."""
        if self.path is not None:
            paths = [self.path]
        else:
            paths = sorted(LOG_DIR.glob("client-*.jsonl"))  # names start with a UTC timestamp
        changed = False
        for p in paths:
            changed |= self._read(p)
        if self.path is None:
            with_run = [p for p in paths if self.files[p]["run_id"]]
            if with_run:
                run_id = self.files[with_run[-1]]["run_id"]
                first = next(p for p in with_run if self.files[p]["run_id"] == run_id)
                # Same game, plus later files that never got a state (failed reconnects).
                paths = [p for p in paths if p >= first and self.files[p]["run_id"] in (run_id, None)]
            else:
                paths = paths[-1:]
        if paths != self.included:
            self.included, changed = paths, True
        return changed

    def entries(self):
        out = []
        for index, p in enumerate(self.included):
            for e in self.files[p]["entries"]:
                out.append(dict(e, _order=(index, e.get("sequence", 0))))
        return out


# ---------------------------------------------------------------- analysis

class Run:
    """Everything the views need, derived from journal entries."""

    def __init__(self, entries, files=()):
        self.files = list(files)
        self.states, self.actions, self.problems = [], [], []
        self.commands = {}
        self.offers, self.transactions = {}, {}  # latest copy of each, keyed by id
        self.state_times = []  # (tick, phase, wall-clock time)
        self.session_ended = False
        self.connected = False
        decisions = []
        tick = None
        for e in entries:
            event, order = e.get("event"), e["_order"]
            if event == "connected":
                self.connected = True
                self.actions.append((order, "connect", {"text": "Connected to the game server", "tick": tick}))
            elif event == "disconnected":
                self.connected = False
                self.actions.append((order, "disconnect", {"text": "Disconnected", "tick": tick}))
            elif event == "session_start":
                self.session_ended = False
            elif event == "session_end":
                self.session_ended = True
            elif event == "connection_error":
                text = f"Could not connect: {e.get('error_type', 'unknown')} (HTTP {e.get('http_status') or 'n/a'})"
                self.problems.append((tick, "bad", text))
                self.actions.append((order, "error", {"text": text, "tick": tick}))
            elif event == "decision" and e.get("action"):
                decisions.append((order, e))
            elif event == "message":
                msg = e.get("message", {})
                if e.get("direction") == "sent":
                    kind = next((k for k in COMMANDS if k in msg), None)
                    if kind:
                        # ready and sync carry no request_id; key them by journal position instead.
                        request_id = msg[kind].get("request_id") or f"{kind}-{order}"
                        self.commands[request_id] = {"kind": kind, "body": msg[kind].get("body", {}), "tick": tick,
                                                     "result": None, "reason": None}
                        self.actions.append((order, kind, self.commands[request_id]))
                elif "state" in msg:
                    s = msg["state"]
                    self.states.append(s)
                    tick = num(s.get("tick"))
                    self.state_times.append((tick, s.get("phase"), parse_time(e.get("timestamp"))))
                    for o in s.get("offers", {}).get("items", []):
                        self.offers[o.get("offer_id")] = o
                    for t in s.get("transactions", {}).get("items", []):
                        self.transactions[t.get("transaction_id")] = t
                elif "result" in msg:
                    result = msg["result"]
                    if result.get("request_id") in self.commands:
                        self.commands[result["request_id"]]["result"] = result
                elif "readiness" in msg:
                    for cmd in self.commands.values():
                        if cmd["kind"] == "ready" and cmd["result"] is None:
                            cmd["result"] = {"ok": bool(msg["readiness"].get("ready")), "code": "readiness not confirmed"}
                elif "protocol_error" in msg:
                    code = msg["protocol_error"].get("code", "unknown")
                    rejected = self.commands.get(nullable(msg["protocol_error"].get("request_id")))
                    if rejected is not None:
                        rejected["result"] = {"ok": False, "code": code}
                    text = f"Server protocol error: {friendly(code)}"
                    self.problems.append((tick, "warn", text))
                    self.actions.append((order, "error", {"text": text, "tick": tick}))

        # Automated decisions are logged just before their command; attach the reason
        # instead of listing the action twice. Keep decisions that sent nothing (advisory mode).
        for order, d in decisions:
            if d.get("request_id") in self.commands:
                self.commands[d["request_id"]]["reason"] = d.get("reason")
            else:
                self.actions.append((order, "advice", {"tick": d.get("tick"), "reason": d.get("reason"),
                                                       "text": f"{d['action']} {d.get('arguments') or ''}".strip()}))
        self.actions.sort(key=lambda a: a[0])

        latest = self.latest = self.states[-1] if self.states else {}
        me = latest.get("self", {})
        rules = latest.get("rules", {})
        self.station = latest.get("self_station_id", "?")
        self.names = {d.get("station_id"): d.get("display_name") for d in latest.get("directory", {}).get("items", [])}
        self.specialty = resource_name(me.get("specialty", "unknown"))
        self.phase = str(latest.get("phase", "")).removeprefix("PHASE_").lower()
        self.first_tick = num(self.states[0].get("tick")) if self.states else 0
        self.last_tick = num(latest.get("tick")) if self.states else 0
        self.duration = num(rules.get("duration_ticks"))
        self.max_health = num(rules.get("max_health")) or 100
        self.tick_ms = num(rules.get("tick_duration_ms"))
        self.health = num(me.get("health")) if self.states else None
        self.inventory = amounts(me.get("inventory"))
        self.upkeep = amounts(me.get("upkeep_per_tick"))
        self.last_update = self.state_times[-1][2] if self.state_times else None

        self.failure_tick = nullable(me.get("first_failure_tick"))
        if self.failure_tick is None and self.states and self.health <= 0:
            self.failure_tick = self.last_tick
        self.failure_tick = None if self.failure_tick is None else num(self.failure_tick)
        unmet = amounts(me.get("last_unmet_upkeep"))
        self.cause = [r for r, u, i, k in zip(RESOURCES, unmet, self.inventory, self.upkeep) if u or i < k]

        self.history = {}  # tick -> (health, inventory); last snapshot of each tick wins
        for s in self.states:
            self.history[num(s.get("tick"))] = (num(s.get("self", {}).get("health")), amounts(s.get("self", {}).get("inventory")))

        self.tx_by_offer = {t.get("offer_id"): t for t in self.transactions.values()}
        cmds = list(self.commands.values())
        self.ads = [c for c in cmds if c["kind"] == "advertise" and (c["result"] or {}).get("ok")]
        self.sent_offers = [c for c in cmds if c["kind"] == "offer"]
        self.created_offers = [c for c in self.sent_offers if (c["result"] or {}).get("ok")]
        self.accepted_out = [c for c in self.created_offers if self.object_id(c) in self.tx_by_offer]
        self.received = sorted((o for o in self.offers.values()
                                if o.get("recipient_id") == self.station and o.get("proposer_id") != self.station),
                               key=lambda o: num(o.get("created_tick")))
        self.accepted_in = [o for o in self.received if o.get("offer_id") in self.tx_by_offer]
        self.waiting = [o for o in self.received if self.offer_state(o.get("offer_id"))[0] == "open"]
        self.rejections = [c for c in cmds if c["result"] is not None and not c["result"].get("ok")]
        self.uptimes = [end - start for start, end in map(self.ad_window, self.ads)]

        for (a, pa, ta), (b, pb, tb) in zip(self.state_times, self.state_times[1:]):
            if b > a + 1:
                self.problems.append((a, "warn", f"Missed ticks {a + 1}–{b - 1} (no update received)"))
            if self.tick_ms and ta and tb and pa == pb == "PHASE_RUNNING":
                gap = (tb - ta).total_seconds()
                if gap > 3 * self.tick_ms / 1000:
                    self.problems.append((a, "warn", f"Stalled {gap:.1f}s between ticks {a} and {b}"))
        if self.states and self.failure_tick is None and not self.connected and self.phase in ("ready", "running", "paused"):
            self.problems.append((self.last_tick, "bad", "Disconnected while the game was still going"))
        self.problems.sort(key=lambda p: p[0] or 0)

    # --- lookups
    def planet(self, station_id, short=False):
        name = self.names.get(station_id)
        if not name or station_id in name:
            return name or station_id or "?"
        return name if short else f"{name} ({station_id})"

    @staticmethod
    def object_id(cmd):
        return nullable((cmd.get("result") or {}).get("object_id"))

    def offer_state(self, offer_id):
        """(key, text) for an offer's final outcome."""
        if offer_id in self.tx_by_offer:
            return "accepted", f"accepted t{self.tx_by_offer[offer_id].get('settled_tick', '?')}"
        o = self.offers.get(offer_id)
        if o is None:
            return "unknown", "outcome not seen"
        status = str(o.get("status", "unknown")).removeprefix("OFFER_STATUS_").lower()
        if status == "open" and self.over():
            return "run_ended", "no answer (game ended)"
        if status == "open":
            return "open", f"waiting (expires t{o.get('expires_tick', '?')})"
        return status, {"expired": "no answer (expired)", "run_ended": "no answer (game ended)",
                        "withdrawn": "withdrawn", "accepted": "accepted"}.get(status, status)

    def ad_window(self, ad):
        start = ad["tick"] if ad["tick"] is not None else self.first_tick
        return start, min(num(ad["body"].get("expires_tick")), self.last_tick)

    def seconds_since_update(self):
        if self.last_update is None:
            return None
        return (datetime.now(timezone.utc) - self.last_update).total_seconds()

    def stalled(self):
        """Seconds without an update, if that is long enough to worry about during a live game."""
        gap = self.seconds_since_update()
        limit = max(3 * self.tick_ms / 1000, 5) if self.tick_ms else 10
        if gap is None or self.session_ended or self.phase != "running" or self.failure_tick is not None:
            return None
        return gap if gap > limit else None

    def verdict(self):
        """(key, text) summarizing where the game stands."""
        if not self.states:
            return ("bad", "CAN'T CONNECT") if self.problems else ("wait", "WAITING FOR DATA")
        if self.failure_tick is not None:
            return "bad", f"FAILED AT TICK {self.failure_tick}"
        if self.phase == "finished":
            return "good", "SURVIVED"
        if self.phase == "aborted":
            return "warn", "GAME ABORTED"
        if self.session_ended or not self.connected:
            return "warn", "CLIENT STOPPED"
        if self.stalled():
            return "bad", f"NO UPDATES {self.stalled():.0f}s"
        return {"ready": ("wait", "WAITING FOR START"), "paused": ("wait", "GAME PAUSED")}.get(self.phase, ("good", "LIVE"))

    def runway(self, index):
        """Whole ticks the current stock covers, or None when there is no upkeep."""
        upkeep = self.upkeep[index]
        return None if not upkeep else self.inventory[index] // upkeep

    def over(self):
        return self.failure_tick is not None or self.phase in ("finished", "aborted") or (self.states and self.session_ended)

    def partners(self):
        """Per planet: [our offers they accepted, our offers to them, theirs we accepted, theirs to us]."""
        stats = {}
        for c in self.created_offers:
            s = stats.setdefault(c["body"].get("recipient_id"), [0, 0, 0, 0])
            s[1] += 1
            s[0] += self.object_id(c) in self.tx_by_offer
        for o in self.received:
            s = stats.setdefault(o.get("proposer_id"), [0, 0, 0, 0])
            s[3] += 1
            s[2] += o.get("offer_id") in self.tx_by_offer
        return stats

    def story(self):
        """Key moments of a finished game, in plain language: (severity, text)."""
        out = []
        ticks = sorted(self.history)
        for i, r in enumerate(RESOURCES):
            short = [t for t in ticks if self.upkeep[i] and self.history[t][1][i] < self.upkeep[i]]
            if short:
                out.append(("bad" if r in self.cause else "warn",
                            f"{r.capitalize()} first ran out at t{short[0]}; short on {len(short)} of {len(ticks)} ticks"))
        low_tick, low = min(((t, self.history[t][0]) for t in ticks), key=lambda x: x[1], default=(None, None))
        first_drop = next((t for t in ticks if self.history[t][0] < self.max_health), None)
        if first_drop is None:
            out.append(("good", "Health never dropped"))
        else:
            out.append(("warn" if low < self.max_health * 0.8 else "info",
                        f"Health first dropped at t{first_drop}; lowest was {low} at t{low_tick}"))
        for i, r in enumerate(RESOURCES):
            left = self.runway(i)
            if left is not None and left > 20:
                out.append(("info", f"Ended with {self.inventory[i]} {r} ({left} ticks' worth) — room to trade or gift more"))
        stats = self.partners()
        done = {p: s[0] + s[2] for p, s in stats.items() if s[0] + s[2]}
        if done:
            best = max(done, key=done.get)
            out.append(("good", f"Best partner: {self.planet(best)} with {done[best]} completed trades"))
        for p, (took, sent, _, _) in stats.items():
            if sent >= 3 and not took:
                out.append(("info", f"{self.planet(p)} never accepted our offers (0/{sent})"))
        return out

    def attention(self):
        """Plain-language alerts, most urgent first: (severity, text)."""
        out = []
        if self.stalled():
            out.append(("bad", f"No update for {self.stalled():.0f}s — the client may be stuck or disconnected"))
        if self.states and self.failure_tick is None and self.phase in ("running", "paused"):
            for i, r in enumerate(RESOURCES):
                left = self.runway(i)
                if left is None:
                    continue
                if left == 0:
                    out.append(("bad", f"Out of {r} — health drops every tick until you get more"))
                elif left <= 3:
                    out.append(("warn", f"{r.capitalize()} runs out in {left} tick{'s' if left != 1 else ''}"))
            ticks = sorted(self.history)
            if len(ticks) > 1:
                past = self.history[ticks[max(0, len(ticks) - 6)]][0]
                if self.health < past:
                    out.append(("warn", f"Health down {past - self.health} over the last {min(5, len(ticks) - 1)} ticks"))
        if self.waiting and not self.over():
            out.append(("info", f"{len(self.waiting)} offer{'s' if len(self.waiting) != 1 else ''} waiting for your answer (tab 3)"))
        recent = [c for c in self.rejections if c["tick"] is not None and c["tick"] >= self.last_tick - 5]
        if recent:
            codes = sorted({friendly(c["result"].get("code")) for c in recent})
            out.append(("warn", f"{len(recent)} command{'s' if len(recent) != 1 else ''} rejected recently: {', '.join(codes)}"))
        out += [(sev, text) for tick, sev, text in self.problems if sev == "bad"][-2:]
        order = {"bad": 0, "warn": 1, "info": 2}
        return sorted(out, key=lambda a: order[a[0]])


# ---------------------------------------------------------------- describing actions

def bundle_text(run, value, glyphs):
    parts = [f"{r}{glyphs['star'] if r == run.specialty else ''} {n}" for r, n in zip(RESOURCES, amounts(value)) if n]
    return ", ".join(parts) or "nothing"


def list_text(run, value, glyphs):
    names = [resource_name(r) for r in (value or {}).get("items", [])]
    return ", ".join(n + (glyphs["star"] if n == run.specialty else "") for n in names) or "nothing"


def describe(run, kind, item, glyphs):
    """One action as (label, detail, status, status_style)."""
    if kind in ("connect", "disconnect", "error"):
        return kind.upper(), item["text"], "", "warn" if kind != "connect" else "dim"
    if kind == "advice":
        return "ADVICE", item["text"], item.get("reason") or "", "dim"
    b = item["body"]
    result = item["result"]
    if result is None:
        status, style = ("" if kind == "sync" else "no server reply"), "warn"
    elif not result.get("ok"):
        status, style = f"rejected: {friendly(result.get('code'))}", "bad"
    else:
        status, style = "done", "dim"
    if kind == "advertise":
        label, detail = "AD", f"selling {list_text(run, b.get('selling'), glyphs)} · seeking {list_text(run, b.get('seeking'), glyphs)}"
        if style == "dim":
            status = f"posted until t{b.get('expires_tick')}"
    elif kind == "offer":
        gift = not any(amounts(b.get("receive")))
        label = "GIFT" if gift else "TRADE"
        detail = f"{glyphs['arrow']} {run.planet(b.get('recipient_id'), short=True)}: {bundle_text(run, b.get('give'), glyphs)}"
        if not gift:
            detail += f" for {bundle_text(run, b.get('receive'), glyphs)}"
        if style == "dim":
            key, status = run.offer_state(run.object_id(item))
            style = {"accepted": "good", "open": "info"}.get(key, "dim")
    elif kind == "accept":
        o = run.offers.get(b.get("offer_id"), {})
        gift = not any(amounts(o.get("receive")))
        label, detail = "ACCEPT", (f"{'gift' if gift else 'trade'} from {run.planet(o.get('proposer_id'), short=True)}: "
                                   f"get {bundle_text(run, o.get('give'), glyphs)}" +
                                   ("" if gift else f" for {bundle_text(run, o.get('receive'), glyphs)}"))
        if style == "dim":
            status, style = "accepted", "good"
    elif kind == "withdraw":
        label, detail = "WITHDRAW", str(b.get("object_id"))
    else:
        label, detail = kind.upper(), {"ready": "declared ready", "sync": "requested a fresh update"}.get(kind, "")
    return label, detail, status, style


# ---------------------------------------------------------------- plain text report

def print_report(run, selected_type="all"):
    g = GLYPHS["unicode" if supports_unicode() else "ascii"]
    print(f"Report for {', '.join(p.name for p in run.files) or 'no journal'}")
    if not run.states:
        print("No state received; this journal does not establish a successful live run.")
    else:
        print(f"Live run {run.latest.get('run_id', 'unknown')} | Planet {run.planet(run.station)} | Specialty: {g['star']} {run.specialty}")
        print(f"Observed ticks: {run.first_tick}–{run.last_tick} | State snapshots: {len(run.states)}")
        if run.failure_tick is not None:
            cause = f" (ran out of {' and '.join(run.cause)})" if run.cause else ""
            print(f"Survived: {run.failure_tick - run.first_tick} ticks | Health ran out at tick {run.failure_tick}{cause}")
        elif run.phase == "finished":
            print(f"Survived the whole game: {run.last_tick - run.first_tick} ticks | Final health {run.health}")
        else:
            print(f"Survived so far: {run.last_tick - run.first_tick} ticks | Health {run.health} at tick {run.last_tick}")
    print(f"Advertisements sent: {len(run.ads)} | Average active time: " +
          (f"{sum(run.uptimes) / len(run.uptimes):.1f} ticks" if run.uptimes else "n/a"))
    rejected = len(run.sent_offers) - len(run.created_offers)
    print(f"Our offers accepted by other planets: {len(run.accepted_out)}/{len(run.created_offers)}" +
          (f" ({rejected} more rejected by the server)" if rejected else ""))
    print(f"Offers from other planets that we accepted: {len(run.accepted_in)}/{len(run.received)}")

    print("\nConnectivity:")
    print("\n".join(f"  {text}" for _, _, text in run.problems) if run.problems else "  No errors, disconnects, or missed ticks")

    print("\nActions (journal order; filter with --type):")
    print("Available types:", ", ".join(sorted({k for _, k, _ in run.actions})) or "none")
    for _, kind, item in run.actions:
        if selected_type != "all" and kind != selected_type:
            continue
        label, detail, status, _ = describe(run, kind, item, g)
        reason = f" — {item['reason']}" if item.get("reason") and kind != "advice" else ""
        when = f"tick {item['tick']}" if item.get("tick") is not None else "before first update"
        print(f"  [{kind}] {when}: {label} {detail}" + (f" ({status})" if status else "") + reason)

    print("\nAdvertisements:")
    if not run.ads:
        print("  None")
    for ad in run.ads:
        start, end = run.ad_window(ad)
        print(f"  ticks {start}–{end} | {ad_range_text(run, start, end)} | offers received while active: "
              f"{sum(1 for o in run.received if start <= num(o.get('created_tick')) <= end)}")

    print("\nReceived offers:")
    if not run.received:
        print("  None observed")
    for o in run.received:
        print(f"  tick {o.get('created_tick', '?')} from {run.planet(o.get('proposer_id'))}: "
              f"they give {bundle_text(run, o.get('give'), g)} for {bundle_text(run, o.get('receive'), g)}"
              f" → {run.offer_state(o.get('offer_id'))[1]}")


def ad_range_text(run, start, end):
    inv = [v[1] for t, v in run.history.items() if start <= t <= end]
    if not inv:
        return "inventory n/a"
    return "inventory " + ", ".join(f"{r} {min(i[n] for i in inv)}–{max(i[n] for i in inv)}" for n, r in enumerate(RESOURCES))


# ---------------------------------------------------------------- terminal drawing

GLYPHS = {
    "unicode": dict(star="★", full="█", empty="░", spark="▁▂▃▄▅▆▇█", arrow="→", good="✓", bad="✗", warn="⚠",
                    info="•", wait="◌", rule="─", dot="·", ell="…", live="●", up="▲", down="▼", lr="←/→", ud="↑/↓", pause="❚❚"),
    "ascii": dict(star="*", full="#", empty=".", spark="_.:-=+*#", arrow="->", good="+", bad="x", warn="!",
                  info="-", wait="o", rule="-", dot="|", ell="~", live="*", up="^", down="v", lr="left/right", ud="up/down", pause="||"),
}
STYLES = {"": "", "dim": "2", "bold": "1", "good": "32", "bad": "1;31", "warn": "33", "info": "36", "wait": "36",
          # One identity colour per resource, shared by its label and its chart.
          "water": "34", "food": "32", "components": "35", "short": "38;5;124",
          "star": "1;33", "title": "1;7", "tab": "7", "key": "1;36", "head": "1"}


class Painter:
    def __init__(self, color, unicode):
        self.color = color
        self.g = GLYPHS["unicode" if unicode else "ascii"]

    def line(self, segments, width):
        """Segments are (text, style) pairs; truncate to width by visible characters."""
        out, used = [], 0
        for text, style in segments:
            if used >= width:
                break
            if used + len(text) > width:
                text = text[:max(0, width - used - 1)] + self.g["ell"]
            used += len(text)
            code = STYLES.get(style, "")
            out.append(f"\x1b[{code}m{text}\x1b[0m" if self.color and code else text)
        return "".join(out)


def bar(value, total, width, g):
    filled = 0 if not total else round(width * max(0, min(value, total)) / total)
    return g["full"] * filled + g["empty"] * (width - filled)


def sparkline(values, width, g, lo=None, hi=None, style=lambda v: "", marks=None):
    """A one-row chart as (text, style) segments. Each cell's style comes from the lowest value it
    covers, so a one-tick shortage still shows when the game is squeezed into fewer cells.
    The tallest block is never used, leaving a gap between stacked charts. Cells whose style is
    in `marks` are drawn as that glyph instead of a bar (a marker, not a value)."""
    if not values:
        return []
    if len(values) > width:  # average into buckets so the whole game fits
        size = len(values) / width
        buckets = [values[int(i * size):max(int((i + 1) * size), int(i * size) + 1)] for i in range(width)]
    else:
        buckets = [[v] for v in values]
    means = [sum(b) / len(b) for b in buckets]
    lo = min(means) if lo is None else lo
    hi = max(means) if hi is None else hi
    levels = g["spark"][:-1]
    segments = []
    for mean, bucket in zip(means, buckets):
        index = len(levels) // 2 if hi <= lo else min(len(levels) - 1, int((mean - lo) / (hi - lo) * (len(levels) - 1) + 0.5))
        cell_style = style(min(bucket))
        glyph = (marks or {}).get(cell_style, levels[index])
        if segments and segments[-1][1] == cell_style:
            segments[-1] = (segments[-1][0] + glyph, cell_style)
        else:
            segments.append((glyph, cell_style))
    return segments


def pad(text, width, right=False):
    return text.rjust(width) if right else text.ljust(width)


def collapse(actions):
    """Merge runs of routine repeats (the automated client re-advertises every tick) into one row:
    (kind, latest item, count, first tick)."""
    out = []
    for _, kind, item in actions:
        if kind == "sync":
            continue
        routine = kind == "advertise" and (item.get("result") or {}).get("ok")
        if routine and out and out[-1][0] == kind and out[-1][4]:
            out[-1] = (kind, item, out[-1][2] + 1, out[-1][3], True)
        else:
            out.append((kind, item, 1, item.get("tick"), routine))
    return [row[:4] for row in out]


TABS = ("Overview", "Timeline", "Offers", "Ads", "Connection")
GLYPH_FOR = {"good": "good", "bad": "bad", "warn": "warn", "info": "wait"}
SEVERITY_STYLE = {"bad": "bad", "warn": "warn", "info": "info", "good": "good", "wait": "wait"}


class Dashboard:
    def __init__(self, source, painter, selected_type="all"):
        self.source, self.p, self.g = source, painter, painter.g
        self.run = Run([])
        self.tab = 0
        self.scroll = [0] * len(TABS)
        self.follow = True  # timeline sticks to the newest entry until you scroll up
        self.filter = selected_type
        self.reasons = False
        self.frozen = False
        self.notice = None
        self.last_scroll_top = 0

    # --- data
    def reload(self):
        if self.frozen or not self.source.refresh():
            return False
        before = len(self.run.files)
        self.run = Run(self.source.entries(), self.source.included)
        if before and len(self.run.files) > before:
            self.notice = (time.monotonic(), f"Reconnected — now combining {len(self.run.files)} journal files")
        return True

    # --- input
    def handle(self, key):
        """Return False to quit."""
        if key in ("q", "Q", "\x03"):
            return False
        if key in "12345" and len(key) == 1:
            self.tab = int(key) - 1
        elif key in ("right", "\t", "l"):
            self.tab = (self.tab + 1) % len(TABS)
        elif key in ("left", "h", "btab"):
            self.tab = (self.tab - 1) % len(TABS)
        elif key in ("up", "k", "down", "j", "pgup", "pgdn", "home", "end", "g", "G"):
            step = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -10, "pgdn": 10}.get(key, 0)
            if self.tab == 1:
                self.follow = key in ("end", "G")
                if key in ("home", "g"):
                    self.scroll[1] = 0
                elif step:
                    self.scroll[1] = self.last_scroll_top + step
            else:
                self.scroll[self.tab] = 0 if key in ("home", "g") else max(0, self.scroll[self.tab] + step)
        elif key == "f":
            types = ["all"] + sorted({k for _, k, _ in self.run.actions})
            self.filter = types[(types.index(self.filter) + 1) % len(types)] if self.filter in types else "all"
            self.tab, self.follow = 1, True
        elif key == "r":
            self.reasons = not self.reasons
        elif key == " ":
            self.frozen = not self.frozen
        return True

    # --- frame
    def frame(self, width, height):
        width = max(20, width)
        if width < 60 or height < 15:
            return [self.p.line([("Make the terminal at least 60×15 to see the dashboard.", "warn")], width)] + [""] * (height - 1)
        body_height = height - 4
        body = [self.overview, self.timeline, self.offers, self.ads, self.connection][self.tab](width, body_height)
        top = self.scroll[self.tab]
        if self.tab == 1 and self.follow:
            top = max(0, len(body) - body_height)
        top = max(0, min(top, len(body) - body_height))
        self.scroll[self.tab] = top
        if self.tab == 1:
            self.last_scroll_top = top
        visible = body[top:top + body_height]
        more = len(body) - top - body_height
        if more > 0 and visible:
            visible[-1] = [(f"  {self.g['down']} {more} more — scroll down to see them", "dim")]
        lines = [self.header(width), self.tabs(width), [(self.g["rule"] * width, "dim")]]
        lines += visible + [[]] * (body_height - len(visible))
        lines.append(self.footer(width))
        return [self.p.line(l, width) for l in lines]

    def header(self, width):
        run, g = self.run, self.g
        left = [(" BAZAAR ", "title"), (" ", "")]
        if run.states:
            left += [(run.planet(run.station), "bold"), (f"  {g['star']} {run.specialty} specialist", "star")]
        else:
            left += [("waiting for a game", "dim")]
        key, text = run.verdict()
        glyph = {"good": g["live"] if text == "LIVE" else g["good"], "bad": g["bad"], "warn": g["warn"], "wait": g["wait"]}[key]
        right = [(f"{glyph} {text}", SEVERITY_STYLE[key])]
        if self.frozen:
            right = [(f"{g['pause']} VIEW FROZEN (space) ", "warn")] + right
        if run.states:
            total = run.duration or run.last_tick
            right += [(f"  tick {run.last_tick}" + (f"/{run.duration}" if run.duration else ""), "bold")]
            if run.duration:
                right += [("  ", ""), (bar(run.last_tick, total, 12, g), "dim")]
        right += [(" ", "")]
        used = lambda segs: sum(len(t) for t, _ in segs)
        if used(left) + used(right) >= width and run.duration:
            right = right[:-3] + right[-1:]  # drop the progress bar
        if used(left) + used(right) >= width and run.states:
            left[-1] = (f"  {g['star']} {run.specialty}", "star")
        return left + [(" " * max(1, width - used(left) - used(right)), "")] + right

    def tabs(self, width):
        segs = [(" ", "")]
        for i, name in enumerate(TABS):
            label = f" {i + 1} {name} "
            if name == "Offers" and self.run.waiting and not self.run.over():
                label = f" {i + 1} {name} ({len(self.run.waiting)} waiting) "
            if name == "Timeline" and self.filter != "all":
                label = f" {i + 1} {name}: {self.filter} "
            if name == "Connection" and self.run.problems:
                label = f" {i + 1} {name} {self.g['warn']} "
            segs += [(label, "tab" if i == self.tab else "dim"), (" ", "")]
        if self.notice and time.monotonic() - self.notice[0] < 8:
            segs += [("  " + self.notice[1], "info")]
        return segs

    def footer(self, width):
        keys = [("1-5", "views"), (self.g["lr"], "switch")]
        if self.tab in (1, 2, 3, 4):
            keys.append((self.g["ud"], "scroll"))
        if self.tab == 1:
            keys += [("f", f"filter: {self.filter}"), ("r", "hide reasons" if self.reasons else "show reasons")]
        keys += [("space", "unfreeze" if self.frozen else "freeze"), ("q", "quit")]
        segs = [(" ", "")]
        for k, what in keys:
            segs += [(k, "key"), (f" {what}   ", "dim")]
        return segs

    # --- views
    def section(self, title, extra=""):
        return [(f"  {title}", "head")] + ([(f"  {extra}", "dim")] if extra else [])

    def overview(self, width, height):
        run, g = self.run, self.g
        lines = [[]]
        if not run.states:
            lines += [[(f"  {g['wait']} No game data yet.", "wait")],
                      [("    Start the client in another terminal, e.g. ", "dim"), ("python client/run_live.py --automate --ready", "bold")],
                      [(f"    Watching {LOG_DIR}", "dim")], []]
            lines += [[(f"  {g['bad']} {text}", "bad")] for _, sev, text in run.problems]
            return lines
        if run.failure_tick is not None:
            cause = f" — ran out of {' and '.join(run.cause)}" if run.cause else ""
            total = f" of {run.duration}" if run.duration else ""
            lines += [[(f"  {g['bad']} PLANET FAILED at tick {run.failure_tick}{total}{cause}", "bad")], []]
        elif run.phase == "finished":
            lines += [[(f"  {g['good']} SURVIVED to the end (tick {run.last_tick}) with {run.health}/{run.max_health} health", "good")], []]

        spark_w = max(10, min(40, width - 62))
        hstyle = "good" if run.health > run.max_health * 0.6 else "warn" if run.health > run.max_health * 0.3 else "bad"
        ticks = sorted(run.history)
        lines.append([("  HEALTH       ", "head"), (bar(run.health, run.max_health, 24, g), hstyle),
                      (f" {run.health:>3}/{run.max_health}   ", hstyle),
                      *sparkline([run.history[t][0] for t in ticks], spark_w, g, 0, run.max_health,
                                 lambda h: "good" if h > run.max_health * 0.6 else "warn" if h > run.max_health * 0.3 else "bad"),
                      (f"  t{ticks[0]}{g['arrow']}t{ticks[-1]}", "dim")])
        lines.append([])
        recent = ticks[-spark_w:]
        # Shortage markers rely on colour; without it the bars simply show the low stock.
        marks = {"short": g["spark"][-2]} if self.p.color else {}
        legend = [(" · ", "dim"), (marks["short"], "short"), (" = below upkeep", "dim")] if marks else []
        lines.append([("  RESOURCES      HAVE  USE/TICK    LASTS       ", "head"), (f"last {len(recent)} ticks", "dim")] + legend)
        for i, r in enumerate(RESOURCES):
            left = run.runway(i)
            if run.over():
                lasts, style, mark = "", "dim", " "
            elif left is None:
                lasts, style, mark = "no upkeep", "dim", " "
            elif left == 0:
                lasts, style, mark = "OUT", "bad", g["bad"]
            else:
                lasts = f"{left} tick{'s' if left != 1 else ''}"
                style, mark = ("warn", g["warn"]) if left <= 3 else ("good", " ")
            star = g["star"] if r == run.specialty else " "
            series = [run.history[t][1][i] for t in recent]
            upkeep = run.upkeep[i]
            lines.append([(f"  {star} ", "star"), (pad(r, 12), r),
                          (pad(str(run.inventory[i]), 5, True), "bold"), (pad(str(upkeep), 10, True), "dim"),
                          (f"    {mark} {pad(lasts, 10)}", style),
                          # Scaled to its own range (printed alongside); red where stock fell below upkeep.
                          *sparkline(series, spark_w, g, style=lambda v: "short" if marks and upkeep and v < upkeep else r, marks=marks),
                          (f" {min(series)}–{max(series)}" if series else "", "dim")])
        lines.append([])
        avg = f"{sum(run.uptimes) / len(run.uptimes):.1f}" if run.uptimes else "–"
        trades = [("  TRADING      ", "head"),
                  ("our offers accepted ", "dim"), (f"{len(run.accepted_out)}/{len(run.created_offers)}", "bold"),
                  (f"   {g['dot']}   offers to us accepted ", "dim"), (f"{len(run.accepted_in)}/{len(run.received)}", "bold")]
        ads = [("ads posted ", "dim"), (str(len(run.ads)), "bold"), (f" (avg {avg} ticks up)", "dim")]
        if sum(len(t) for t, _ in trades + ads) + 7 <= width:
            lines.append(trades + [(f"   {g['dot']}   ", "dim")] + ads)
        else:
            lines += [trades, [(" " * 15, "")] + ads]
        lines.append([])

        # Live: what needs attention now. Afterwards: the story of the game.
        title, alerts = ("WHAT HAPPENED", run.story()) if run.over() else ("NEEDS ATTENTION", run.attention())
        lines.append([(f"  {title}", "head")])
        if not alerts:
            lines.append([(f"    {g['good']} Nothing right now", "good")])
        # After the game the story matters more than the activity feed, so let it take the room.
        limit = max(2, min(6, height - len(lines) - (2 if run.over() else 6)))
        for sev, text in alerts[:limit]:
            lines.append([(f"    {g[sev]} {text}", SEVERITY_STYLE[sev])])
        if len(alerts) > limit:
            lines.append([(f"    … and {len(alerts) - limit} more", "dim")])
        lines.append([])

        room = height - len(lines) - 1
        if room > 1:
            lines.append(self.section("RECENT ACTIVITY", "newest first · full history on tab 2"))
            for kind, item, count, first in collapse(run.actions)[-(room - 1):][::-1]:
                lines.append(self.action_row(kind, item, width, count, first))
        return lines

    def action_row(self, kind, item, width, count=1, first=None):
        label, detail, status, style = describe(self.run, kind, item, self.g)
        tick = item.get("tick")
        when = f"t{tick}" if tick is not None else ""
        if count > 1:
            label, when = f"{label} ×{count}", f"t{first}–{tick}"
            detail = "latest: " + detail
        status_w = min(30, max(12, width // 4))
        detail_w = width - 4 - 9 - 11 - status_w - 2
        if len(detail) > detail_w:
            detail = detail[:detail_w - 1] + self.g["ell"]
        label_style = {"TRADE": "info", "GIFT": "star", "ACCEPT": "good", "ERROR": "bad", "DISCONNECT": "warn"}.get(label, "bold")
        glyph = {"good": self.g["good"], "bad": self.g["bad"], "warn": self.g["warn"], "info": self.g["wait"]}.get(style, " ")
        return [(f"    {pad(when, 9)}", "dim"), (pad(label, 11), label_style),
                (pad(detail, detail_w), ""), ("  ", ""), (f"{glyph} {status}", style)]

    def timeline(self, width, height):
        run = self.run
        rows = [a for a in run.actions if self.filter == "all" or a[1] == self.filter]
        lines = [[("  Every action in order. ", "dim"),
                  (f"{len(rows)} shown" + (f" (filter: {self.filter}, press f to change)" if self.filter != "all" else ""), "dim")], []]
        if not rows:
            lines.append([("    Nothing yet.", "dim")])
        for _, kind, item in rows:
            lines.append(self.action_row(kind, item, width))
            if self.reasons and item.get("reason") and kind != "advice":
                lines.append([(" " * 19, ""), (f"why: {item['reason']}", "dim")])
        return lines

    def offers(self, width, height):
        run, g = self.run, self.g
        lines = []
        if run.waiting and not run.over():
            lines += [[], [("  WAITING FOR YOUR ANSWER", "info"), ("   accept with: accept OFFER_ID", "dim")]]
            for o in run.waiting:
                lines.append([(f"    {pad(o.get('offer_id', '?'), 14)}", "bold"), (pad(run.planet(o.get('proposer_id')), 20), ""),
                              (f"they give {bundle_text(run, o.get('give'), g)} for {bundle_text(run, o.get('receive'), g)}", ""),
                              (f"   expires t{o.get('expires_tick', '?')}", "info")])
        stats = run.partners()
        if stats:
            lines += [[], [(f"  {pad('PARTNERS', 26)}{pad('TOOK OUR OFFERS', 18)}{pad('WE TOOK THEIRS', 18)}TRADES DONE", "head")]]
            for planet, (took, sent, we_took, got) in sorted(stats.items(), key=lambda kv: -(kv[1][0] + kv[1][2])):
                lines.append([(f"    {pad(run.planet(planet), 24)}", ""),
                              (pad(f"{took}/{sent}", 18), "bad" if sent >= 3 and not took else ""),
                              (pad(f"{we_took}/{got}", 18), ""), (str(took + we_took), "good" if took + we_took else "dim")])
        rejected = len(run.sent_offers) - len(run.created_offers)
        lines += [[], [("  OFFERS WE SENT  ", "head"), (f"{len(run.accepted_out)} accepted out of {len(run.created_offers)}" +
                                                         (f" · {rejected} rejected by the server" if rejected else ""), "dim")]]
        lines.append([(f"    {pad('TICK', 6)}{pad('TO', 22)}{pad('TYPE', 7)}{pad('WE GIVE', 18)}{pad('WE GET', 18)}RESULT", "dim")])
        for c in run.sent_offers:
            b = c["body"]
            gift = not any(amounts(b.get("receive")))
            _, _, status, style = describe(run, "offer", c, g)
            lines.append([(f"    {pad(f't{c['tick']}', 6)}", "dim"), (pad(run.planet(b.get('recipient_id')), 22), ""),
                          (pad("GIFT" if gift else "TRADE", 7), "star" if gift else "info"),
                          (pad(bundle_text(run, b.get('give'), g), 18), ""), (pad("-" if gift else bundle_text(run, b.get('receive'), g), 18), ""),
                          (f"{GLYPH_FOR.get(style, ' ') and g[GLYPH_FOR[style]] if style in GLYPH_FOR else ' '} {status}", style)])
        if not run.sent_offers:
            lines.append([("    None yet", "dim")])
        lines += [[], [("  OFFERS SENT TO US  ", "head"), (f"we accepted {len(run.accepted_in)} out of {len(run.received)}", "dim")]]
        lines.append([(f"    {pad('TICK', 6)}{pad('FROM', 22)}{pad('TYPE', 7)}{pad('THEY GIVE', 18)}{pad('THEY WANT', 18)}RESULT", "dim")])
        for o in run.received:
            key, status = run.offer_state(o.get("offer_id"))
            style = {"accepted": "good", "open": "info"}.get(key, "dim")
            status = f"{g[GLYPH_FOR[style]] if style in GLYPH_FOR else ' '} {status}"
            gift = not any(amounts(o.get("receive")))
            lines.append([(f"    {pad(f't{o.get('created_tick', '?')}', 6)}", "dim"), (pad(run.planet(o.get('proposer_id')), 22), ""),
                          (pad("GIFT" if gift else "TRADE", 7), "star" if gift else "info"),
                          (pad(bundle_text(run, o.get('give'), g), 18), ""),
                          (pad("-" if gift else bundle_text(run, o.get('receive'), g), 18), ""),
                          (status, style)])
        if not run.received:
            lines.append([("    None yet", "dim")])
        return lines

    def ads(self, width, height):
        run, g = self.run, self.g
        avg = f"{sum(run.uptimes) / len(run.uptimes):.1f} ticks" if run.uptimes else "–"
        lines = [[], [("  OUR ADVERTISEMENTS  ", "head"), (f"{len(run.ads)} posted · average time up {avg}", "dim")],
                 [(f"    {pad('ACTIVE', 11)}{pad('SELLING', 26)}{pad('SEEKING', 22)}{pad('OFFERS IN', 11)}INVENTORY WHILE UP", "dim")]]
        for ad in run.ads:
            start, end = run.ad_window(ad)
            during = sum(1 for o in run.received if start <= num(o.get("created_tick")) <= end)
            lines.append([(f"    {pad(f't{start}–{end}', 11)}", "dim"), (pad(list_text(run, ad['body'].get('selling'), g), 26), ""),
                          (pad(list_text(run, ad['body'].get('seeking'), g), 22), ""), (pad(str(during), 11), "good" if during else "dim"),
                          (ad_range_text(run, start, end).removeprefix("inventory "), "dim")])
        if not run.ads:
            lines.append([("    None yet", "dim")])
        return lines

    def connection(self, width, height):
        run, g = self.run, self.g
        since = run.seconds_since_update()
        key, text = run.verdict()
        lines = [[], [("  STATUS       ", "head"), (text, SEVERITY_STYLE[key])]]
        if since is not None and not run.over():
            lines.append([("  LAST UPDATE  ", "head"), (f"{since:.1f}s ago", "bad" if run.stalled() else ""),
                          (f"  (a tick is {run.tick_ms / 1000:g}s)" if run.tick_ms else "", "dim")])
        lines.append([("  RECEIVED     ", "head"), (f"{len(run.states)} updates · ticks {run.first_tick}–{run.last_tick}", "")])
        lines.append([("  SENT         ", "head"), (f"{len(run.commands)} commands · {len(run.rejections)} rejected", "")])
        lines += [[], [("  JOURNAL FILES", "head"), (f"  in {run.files[0].parent if run.files else LOG_DIR}", "dim")],
                  [("    a reconnect starts a new file; this view combines all files from the same game", "dim")]]
        for p in run.files:
            lines.append([(f"    {p.name}", "dim")])
        lines += [[], [("  PROBLEMS", "head")]]
        if not run.problems:
            lines.append([(f"    {g['good']} No errors, disconnects, missed ticks, or stalls", "good")])
        for tick, sev, text in run.problems:
            lines.append([(f"    {pad(f't{tick}' if tick is not None else '', 6)}", "dim"), (f"{g[sev]} {text}", SEVERITY_STYLE[sev])])
        if run.rejections:
            lines += [[], [("  REJECTED COMMANDS", "head")]]
            for c in run.rejections:
                label, detail, status, style = describe(run, c["kind"], c, g)
                lines.append([(f"    {pad(f't{c['tick']}', 6)}", "dim"), (f"{label} {detail}  ", ""), (status, "bad")])
        return lines


# ---------------------------------------------------------------- keyboard + main loop

class Keyboard:
    """Non-blocking single-key input on POSIX terminals and Windows consoles."""
    SEQUENCES = {"\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left", "\x1b[Z": "btab",
                 "\x1b[5~": "pgup", "\x1b[6~": "pgdn", "\x1b[H": "home", "\x1b[F": "end",
                 "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left"}
    WINDOWS = {"H": "up", "P": "down", "M": "right", "K": "left", "I": "pgup", "Q": "pgdn", "G": "home", "O": "end"}

    def __enter__(self):
        self.windows = os.name == "nt"
        if not self.windows:
            import termios, tty
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        if not self.windows:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def read(self, timeout):
        if self.windows:
            import msvcrt
            end = time.monotonic() + timeout
            keys = []
            while time.monotonic() < end and not keys:
                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    keys.append(self.WINDOWS.get(msvcrt.getwch(), "") if ch in ("\x00", "\xe0") else ch)
                time.sleep(0.03)
            return keys
        import select
        if not select.select([self.fd], [], [], timeout)[0]:
            return []
        data = os.read(self.fd, 64).decode(errors="ignore")
        keys = []
        while data:
            for seq, name in self.SEQUENCES.items():
                if data.startswith(seq):
                    keys.append(name)
                    data = data[len(seq):]
                    break
            else:
                keys.append(data[0])
                data = data[1:]
        return keys


def supports_unicode():
    return (sys.stdout.encoding or "").lower().replace("-", "") in ("utf8", "utf16")


def run_dashboard(source, selected_type):
    painter = Painter(color=not os.environ.get("NO_COLOR"), unicode=supports_unicode())
    if os.name == "nt":
        os.system("")  # turns on ANSI escape handling in the Windows console
    dash = Dashboard(source, painter, selected_type)
    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
    try:
        with Keyboard() as keyboard:
            last_draw = 0
            dirty = True
            while True:
                dirty |= dash.reload()
                if dirty or time.monotonic() - last_draw > 1:  # redraw each second for "last update" clocks
                    size = shutil.get_terminal_size()
                    lines = dash.frame(size.columns, size.lines)
                    out.write("\x1b[H" + "\n".join(l + "\x1b[K" for l in lines) + "\x1b[J")
                    out.flush()
                    last_draw, dirty = time.monotonic(), False
                for key in keyboard.read(0.25):
                    if not dash.handle(key):
                        return
                    dirty = True
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\x1b[?25h\x1b[?1049l")
        out.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, nargs="?",
                        help="a specific journal; by default follows the newest game in run/logs (reconnects included)")
    parser.add_argument("--type", default="all", help="show only one action type, e.g. offer (or 'all')")
    parser.add_argument("--plain", action="store_true", help="print a text report instead of the interactive dashboard")
    parser.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)  # older name for the live view
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # never crash on a console that lacks a symbol
    if args.log is not None and not args.log.is_file():
        parser.error(f"Journal does not exist: {args.log}")
    source = Source(args.log)
    if not args.plain and sys.stdout.isatty() and sys.stdin.isatty():
        run_dashboard(source, args.type)
    else:
        source.refresh()
        if not source.included:
            parser.error(f"No client journal found in {LOG_DIR}; start run_live.py first or provide a log path.")
        print_report(Run(source.entries(), source.included), args.type)
