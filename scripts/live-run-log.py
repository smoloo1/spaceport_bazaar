"""Create a readable action report from a live client's JSONL journal."""
import argparse
import json
from pathlib import Path
import time


RESOURCES = ("water", "food", "components")


def bundle(value):
    if not isinstance(value, dict):
        return "?"
    return ", ".join(f"{name} {value.get(name, 0)}" for name in RESOURCES)


def load_journal(path):
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"Warning: skipped incomplete or invalid JSON on line {number}.")
    return entries


def nullable(value):
    """Read protobuf JSON nullable wrappers; return None for a null wrapper."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def summarize(path, selected_type="all"):
    entries = load_journal(path)
    states, outgoing_cmds, actions, incoming, errors = [], {}, [], {}, []
    snapshots = []
    connected = False
    for e in entries:
        event = e.get("event")
        if event == "connected":
            connected = True
            actions.append((e.get("sequence", 0), "connect", "Connected"))
        elif event == "disconnected":
            connected = False
            actions.append((e.get("sequence", 0), "disconnect", "Disconnected"))
        elif event == "connection_error":
            errors.append(f"Connection error: {e.get('error_type', 'unknown')} (HTTP {e.get('http_status') or 'n/a'})")
            actions.append((e.get("sequence", 0), "connectivity", errors[-1]))
        elif event == "message":
            msg = e.get("message", {})
            if e.get("direction") == "sent":
                kind = next((k for k in ("advertise", "offer", "accept", "withdraw", "ready", "sync") if k in msg), None)
                if kind:
                    body = msg[kind].get("body", {})
                    request_id = msg[kind].get("request_id")
                    item = {"kind": kind, "body": body, "tick": None,
                            "request_id": request_id, "sequence": e.get("sequence", 0), "result": None}
                    outgoing_cmds[request_id] = item
                    actions.append((item["sequence"], kind, item))
            elif "state" in msg:
                s = msg["state"]
                states.append(s)
                snapshots.append((int(s.get("tick", 0)), e.get("sequence", 0)))
                me = s.get("self", {})
                for o in s.get("offers", {}).get("items", []):
                    if o.get("recipient_id") == s.get("self_station_id"):
                        incoming.setdefault(o.get("offer_id"), o)
            elif "result" in msg:
                result = msg["result"]
                if result.get("request_id") in outgoing_cmds:
                    outgoing_cmds[result["request_id"]]["result"] = result
            elif "protocol_error" in msg:
                code = msg["protocol_error"].get("code", "unknown")
                errors.append(f"Protocol error: {code}")
                actions.append((e.get("sequence", 0), "connectivity", errors[-1]))
        elif event == "decision" and e.get("action"):
            cmd = outgoing_cmds.get(e.get("request_id"))
            if cmd:
                cmd["tick"] = e.get("tick")
            else:
                actions.append((e.get("sequence", 0), "decision", {
                    "kind": e["action"], "tick": e.get("tick"), "reason": e.get("reason"),
                    "arguments": e.get("arguments"), "request_id": e.get("request_id")}))

    latest = states[-1] if states else {}
    station = latest.get("self_station_id", "unknown")
    specialty_num = latest.get("self", {}).get("specialty")
    specialty = {"RESOURCE_WATER": "water", "RESOURCE_FOOD": "food",
                 "RESOURCE_COMPONENTS": "components"}.get(str(specialty_num), str(specialty_num or "unknown"))
    def status(item):
        r = item.get("result") or {}
        return "accepted" if r.get("ok") else ("rejected" if r else "pending / unconfirmed")

    ads = [i for i in outgoing_cmds.values() if i["kind"] == "advertise"]
    offers = [i for i in outgoing_cmds.values() if i["kind"] == "offer"]
    accepted_out = sum(1 for s in states for t in s.get("transactions", {}).get("items", [])
                       if t.get("proposer_id") == station)
    accepted_in = sum(1 for s in states for t in s.get("transactions", {}).get("items", [])
                      if t.get("recipient_id") == station)
    # State snapshots repeat completed transactions, so deduplicate by id.
    accepted_out_ids = {t.get("transaction_id") for s in states for t in s.get("transactions", {}).get("items", [])
                        if t.get("proposer_id") == station}
    accepted_in_ids = {t.get("transaction_id") for s in states for t in s.get("transactions", {}).get("items", [])
                       if t.get("recipient_id") == station}
    received_ids = {oid for oid, o in incoming.items() if o.get("proposer_id") != station}
    ad_active_ticks = []
    for ad in ads:
        body = ad["body"]
        start = ad.get("tick")
        end = int(body.get("expires_tick", start or 0))
        if start is not None and end >= int(start):
            ad_active_ticks.append(end - int(start))

    if not latest:
        print("No state received; this journal does not establish a successful live run.")
    else:
        print(f"Live run {latest.get('run_id', 'unknown')} | Planet {station} | Specialty: ★ {specialty}")
        print(f"Observed ticks: {states[0].get('tick')}–{latest.get('tick')} | State snapshots: {len(states)}")
        first_tick = int(states[0].get("tick", 0))
        last_tick = int(latest.get("tick", first_tick))
        me = latest.get("self", {})
        failure_tick = nullable(me.get("first_failure_tick"))
        if failure_tick is None and int(me.get("health", 1)) <= 0:
            failure_tick = last_tick
        if failure_tick is not None:
            survived = max(0, int(failure_tick) - first_tick)
            print(f"Survived: {survived} ticks | Health ran out and planet failed/disconnected at tick {failure_tick}")
        else:
            print(f"Survived so far: {max(0, last_tick - first_tick)} ticks | "
                  f"No health failure observed through tick {last_tick}")
    print(f"Advertisements sent: {len(ads)} | Average advertised uptime: " +
          (f"{sum(ad_active_ticks) / len(ad_active_ticks):.2f} ticks" if ad_active_ticks else "n/a"))
    print(f"Offers accepted by other planets / sent: {len(accepted_out_ids)}/{len(offers)}")
    print(f"Offers accepted from other planets / received: {len(accepted_in_ids)}/{len(received_ids)}")
    print("Connectivity:", "; ".join(errors) if errors else "no connection or protocol errors recorded")
    gaps = [(a, b) for (a, _), (b, _) in zip(snapshots, snapshots[1:]) if b > a + 1]
    if gaps:
        print("Snapshot gaps (possible missed observation; not proof of downtime): " +
              ", ".join(f"{a}→{b}" for a, b in gaps))

    print("\nActions (journal order; filter with --type):")
    known_types = sorted({kind for _, kind, _ in actions})
    print("Available types:", ", ".join(known_types) if known_types else "none")
    for _, kind, item in sorted(actions, key=lambda a: a[0]):
        if selected_type != "all" and kind != selected_type:
            continue
        if isinstance(item, str):
            print(f"  [{kind}] {item}")
        elif kind == "advertise":
            b = item["body"]
            print(f"  [advertise] tick {item['tick']} sell={b.get('selling', {}).get('items', [])} "
                  f"seek={b.get('seeking', {}).get('items', [])} expires={b.get('expires_tick')} ({status(item)})")
        elif kind == "offer":
            b = item["body"]
            gift = not any((b.get("receive") or {}).values())
            print(f"  [offer] tick {item['tick']} to {b.get('recipient_id', '?')} "
                  f"{'GIFT' if gift else 'TRADE'} give=({bundle(b.get('give'))}) "
                  f"receive=({bundle(b.get('receive'))}) ({status(item)})")
        elif kind in ("accept", "withdraw"):
            print(f"  [{kind}] tick {item['tick']} {item['body']} ({status(item)})")
        else:
            print(f"  [{kind}] tick {item.get('tick')} {item.get('kind')} {item.get('reason', '')} {item.get('arguments', '')}")

    print("\nAdvertisements detail:")
    for ad in ads:
        b = ad["body"]
        aid = (ad.get("result") or {}).get("object_id", {}).get("value")
        observed = [s for s in states if aid and any(x.get("advertisement_id") == aid
                    for x in s.get("advertisements", {}).get("items", []))]
        inv = [tuple(s.get("self", {}).get("inventory", {}).get(r, 0) for r in RESOURCES) for s in observed]
        active_offers = sum(1 for o in incoming.values() if int(o.get("created_tick", -1)) >=
                            (min((int(s.get("tick", 0)) for s in observed), default=10**20))) if observed else 0
        print(f"  tick {min((int(s.get('tick',0)) for s in observed), default='?')}–{b.get('expires_tick')} "
              f"inventory range water/food/components={tuple((min(x[i] for x in inv), max(x[i] for x in inv)) for i in range(3)) if inv else 'n/a'}; "
              f"offers received during observed active window={active_offers}")
    print("\nReceived offers:")
    if not received_ids:
        print("  None observed")
    for oid in sorted(received_ids):
        o = incoming[oid]
        state = str(o.get("status", "unknown"))
        print(f"  {oid} from {o.get('proposer_id', '?')} created tick {o.get('created_tick', '?')}: {state}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, nargs="?", help="live client JSONL journal; defaults to the newest journal in run/logs")
    parser.add_argument("--generate-log", action="store_true", help="generate a report from the active live journal")
    parser.add_argument("--type", default="all", help="show only an action type (or 'all')")
    parser.add_argument("--watch", action="store_true", help="refresh the report every 2 seconds while the live client writes the journal")
    args = parser.parse_args()
    if args.log is None:
        log_dir = Path(__file__).resolve().parents[1] / "run" / "logs"
        candidates = sorted(log_dir.glob("client-*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            parser.error(f"No client journal found in {log_dir}; start run_live.py first or provide a log path.")
        args.log = candidates[-1]
    if not args.log.is_file():
        parser.error(f"Journal does not exist: {args.log}")
    if args.watch:
        try:
            while True:
                print("\x1b[2J\x1b[H", end="")
                summarize(args.log, args.type)
                time.sleep(2)
        except KeyboardInterrupt:
            pass
    else:
        summarize(args.log, args.type)
