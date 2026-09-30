"""Deterministic counterfactual scenarios, not a reconstruction of classmates' decisions."""
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client'))
import bazaar
import bazaar_pb2 as pb
from strategy import Policy, candidates

RESOURCES = ('water', 'food', 'components')


def vec(bundle):
    return [bundle[r] for r in RESOURCES]


@dataclass(frozen=True)
class Scenario:
    name: str
    delay: int = 0
    ignore_target: bool = False
    peer_trade_size: int = 5


class Simulation:
    def __init__(self, log, target_policy, scenario, conditions="recorded"):
        self.conditions = conditions
        self.rules = log['snapshot']['rules']
        self.duration = self.rules['duration_ticks']
        self.scenario = scenario
        self.target = 'P02'
        metadata = log['snapshot']['stations']
        self.ids = {s['display_id']: s['station_id'] for s in metadata}
        last_by_tick = {h['tick']: h for h in log['history']}
        self.production = {t: {self.ids[s['display_id']]: vec(s['last_production'])
                               for s in h['stations']} for t, h in last_by_tick.items()}
        initial = {self.ids[s['display_id']]: s for s in last_by_tick[0]['stations']}
        totals = {self.ids[s['display_id']]: s for s in log['snapshot']['economic']['stations']}
        self.nodes = {}
        for sid, s in initial.items():
            self.nodes[sid] = dict(inventory=vec(s['initial_inventory']), upkeep=vec(s['upkeep_per_tick']),
                specialty=max(range(3), key=lambda i: vec(totals[sid]['produced_total'])[i]) + 1,
                health=s['health'], failure=None, shortages=0, imports=[0]*3, exports=[0]*3,
                gifts_sent=0, trades=0, rejected=0, commands=0, expired=0,
                policy=target_policy if sid == self.target else Policy(trade_size=scenario.peer_trade_size))
        if conditions == 'low_start':
            self.nodes[self.target]['inventory'] = [8, 6, 6]
        elif conditions == 'uneven_start':
            self.nodes[self.target]['inventory'] = [40, 2, 18]
        elif conditions == 'abundant_start':
            self.nodes[self.target]['inventory'] = [50, 50, 50]
        elif conditions == 'higher_upkeep':
            self.nodes[self.target]['upkeep'] = [1, 2, 1]
        elif conditions == 'production_outage':
            for tick in range(15, 36):
                self.production[tick][self.target] = [0, 0, 0]
        elif conditions in ('food_specialty', 'components_specialty'):
            # Rotate the whole world's resource labels, preserving balanced supply.
            shift = 1 if conditions == 'food_specialty' else 2
            def rotate(values):
                return values[-shift:] + values[:-shift]
            for n in self.nodes.values():
                n['inventory'] = rotate(n['inventory'])
                n['upkeep'] = rotate(n['upkeep'])
                n['specialty'] = (n['specialty'] - 1 + shift) % 3 + 1
            for production in self.production.values():
                for sid in production:
                    production[sid] = rotate(production[sid])
        elif conditions != 'recorded':
            raise ValueError(conditions)
        self.initial_target = {k: (v[:] if isinstance(v, list) else v)
                               for k, v in self.nodes[self.target].items()
                               if k in ('inventory', 'upkeep', 'specialty')}
        self.offers = []
        self.ads = {}
        self.tick = 0
        self.counter = 0
        self.timeline = []

    def state(self, sid):
        node = self.nodes[sid]
        s = pb.State(run_id='simulation', tick=self.tick, phase=pb.PHASE_RUNNING,
                     self_station_id=sid)
        s.self.inventory.CopyFrom(bazaar.bundle(*node['inventory']))
        s.self.upkeep_per_tick.CopyFrom(bazaar.bundle(*node['upkeep']))
        s.self.specialty = node['specialty']
        s.self.health = node['health']
        s.self.failed_once = node['failure'] is not None
        for key, value in self.rules.items():
            if key not in ('resource_order',):
                setattr(s.rules, key, value)
        for peer in self.nodes:
            s.directory.items.add(station_id=peer, display_name=peer)
        for a in self.ads.values():
            if a.expires_tick > self.tick:
                s.advertisements.items.add().CopyFrom(a)
        for o in self.offers:
            # Response delay models when a recipient processes an offer. Its
            # proposer still sees the commitment immediately.
            if o.recipient_id == sid and o.proposer_id != sid:
                if self.scenario.ignore_target and self.target in (o.proposer_id, o.recipient_id):
                    continue
                if self.tick < o.created_tick + self.scenario.delay:
                    continue
            if sid in (o.proposer_id, o.recipient_id):
                s.offers.items.add().CopyFrom(o)
        return s

    def execute(self, sid, action):
        n = self.nodes[sid]
        n['commands'] += 1
        self.counter += 1
        ref = f'sim-{self.counter}'
        if action.kind == 'advertise':
            selling, seeking, expiry = action.args
            a = pb.Advertisement(advertisement_id=ref, station_id=sid,
                expires_tick=expiry, status=pb.PUBLICATION_STATUS_ACTIVE)
            a.selling.items.extend(selling)
            a.seeking.items.extend(seeking)
            self.ads[sid] = a
        elif action.kind == 'offer':
            peer, give, receive, expiry = action.args
            o = pb.Offer(offer_id=ref, proposer_id=sid, recipient_id=peer,
                         created_tick=self.tick, expires_tick=expiry, status=pb.OFFER_STATUS_OPEN)
            o.give.CopyFrom(bazaar.bundle(*give))
            o.receive.CopyFrom(bazaar.bundle(*receive))
            self.offers.append(o)
        elif action.kind == 'withdraw':
            for o in self.offers:
                if o.offer_id == action.args[0] and o.proposer_id == sid:
                    o.status = pb.OFFER_STATUS_WITHDRAWN
            if sid in self.ads and self.ads[sid].advertisement_id == action.args[0]:
                del self.ads[sid]
        elif action.kind == 'accept':
            o = next(o for o in self.offers if o.offer_id == action.args[0])
            a, b = self.nodes[o.proposer_id], self.nodes[o.recipient_id]
            give, receive = bazaar.as_tuple(o.give), bazaar.as_tuple(o.receive)
            if (o.status != pb.OFFER_STATUS_OPEN or o.expires_tick <= self.tick
                    or a['failure'] is not None or b['failure'] is not None
                    or any(a['inventory'][i] < give[i] or b['inventory'][i] < receive[i] for i in range(3))):
                n['rejected'] += 1
                return
            before = [a['inventory'][i] + b['inventory'][i] for i in range(3)]
            for i in range(3):
                a['inventory'][i] += receive[i] - give[i]
                b['inventory'][i] += give[i] - receive[i]
                a['imports'][i] += receive[i]
                a['exports'][i] += give[i]
                b['imports'][i] += give[i]
                b['exports'][i] += receive[i]
                assert a['inventory'][i] + b['inventory'][i] == before[i]
                assert min(a['inventory'][i], b['inventory'][i]) >= 0
            if not any(receive):
                a['gifts_sent'] += sum(give)
            if not any(give):
                b['gifts_sent'] += sum(receive)
            a['trades'] += 1
            b['trades'] += 1
            o.status = pb.OFFER_STATUS_ACCEPTED

    def run(self):
        for tick in range(self.duration + 1):
            self.tick = tick
            if tick:
                for sid, n in self.nodes.items():
                    n['inventory'] = [a+b for a,b in zip(n['inventory'],self.production[tick][sid])]
                    unmet = sum(max(0, u-i) for i,u in zip(n['inventory'], n['upkeep']))
                    n['inventory'] = [max(0,i-u) for i,u in zip(n['inventory'], n['upkeep'])]
                    if n['failure'] is None:
                        if unmet:
                            n['shortages'] += 1
                            n['health'] = max(0, n['health'] - unmet*self.rules['shortage_damage_per_unit'])
                        else:
                            n['health'] = min(self.rules['max_health'], n['health']+self.rules['recovery_per_fully_supplied_tick'])
                        if n['health'] == 0:
                            n['failure'] = tick
            for o in self.offers:
                if o.status == pb.OFFER_STATUS_OPEN and o.expires_tick <= tick:
                    o.status = pb.OFFER_STATUS_EXPIRED
                    self.nodes[o.proposer_id]['expired'] += 1
            # Closed offers do not affect decisions; counters retain outcomes.
            self.offers = [o for o in self.offers if o.status == pb.OFFER_STATUS_OPEN]
            if tick < self.duration:
                attempted = {sid:set() for sid in self.nodes}
                budgets = Counter()
                # Rotate deterministic scheduling to reduce fixed first-player bias.
                order = sorted(self.nodes)
                order = order[tick % len(order):] + order[:tick % len(order)]
                for _ in range(self.rules['new_commands_per_station_per_tick'] * len(order)):
                    progress = False
                    for sid in order:
                        n = self.nodes[sid]
                        if (n['policy'] is None or n['failure'] is not None
                            or budgets[sid] >= self.rules['new_commands_per_station_per_tick']
                            or n['commands'] >= self.rules['max_request_records_per_station']):
                            continue
                        s = self.state(sid)
                        action = next((a for a in candidates(s,n['policy'])
                                       if a.key not in attempted[sid]), None)
                        if action is None:
                            continue
                        attempted[sid].add(action.key)
                        if len(action.message(s, ref_id(sid, tick, budgets[sid])).SerializeToString()) > self.rules['max_command_bytes']:
                            continue
                        self.execute(sid, action)
                        budgets[sid] += 1
                        progress = True
                    if not progress:
                        break
            n = self.nodes[self.target]
            self.timeline.append(dict(tick=tick, health=n['health'], inventory=n['inventory'][:],
                                      failed=n['failure'] is not None))
        n = self.nodes[self.target]
        return dict(conditions=self.conditions, initial_target=self.initial_target, scenario=self.scenario.name, survived=n['failure'] is None,
                    failure_tick=n['failure'], final_health=n['health'],
                    shortage_ticks_before_failure=n['shortages'], final_inventory=n['inventory'],
                    imported=n['imports'], gifts_delivered=n['gifts_sent'], trades=n['trades'],
                    expired_offers=n['expired'], rejected=n['rejected'], commands=n['commands'],
                    planets_survived=sum(v['failure'] is None for v in self.nodes.values()))


def ref_id(sid, tick, number):
    return f'{sid}-{tick}-{number}'


def compare(log):
    policies = {'inactive':None, 'current':Policy(),
                'earlier_only':Policy(imported_seek_ticks=10),
                'larger_only':Policy(trade_size=5),
                'earlier_and_larger':Policy(imported_seek_ticks=10,trade_size=5)}
    scenarios = [Scenario('responsive'), Scenario('one_tick_delay',delay=1),
                 Scenario('three_tick_delay',delay=3), Scenario('ignore_P02',ignore_target=True),
                 Scenario('small_trade_peers',peer_trade_size=2)]
    conditions = ('recorded', 'low_start', 'uneven_start', 'abundant_start',
                  'higher_upkeep', 'production_outage', 'food_specialty', 'components_specialty')
    results, timelines = [], {}
    for condition in conditions:
        for scenario in scenarios:
            for name, policy in policies.items():
                sim = Simulation(log,policy,scenario,condition)
                result = sim.run()
                result['policy'] = name
                result['settings'] = asdict(policy) if policy else None
                results.append(result)
                timelines[f'{condition}/{scenario.name}/{name}'] = sim.timeline
                if policy is None and condition == 'recorded':
                    assert result['failure_tick'] == 40 and result['trades'] == 0
                    assert result['final_inventory'] == [450,0,0]
        print(f'Completed condition: {condition}', flush=True)
    return results, timelines



def write_report(results, path):
    lines = ["# Strategy comparison: varied starting conditions", "",
             "200 deterministic 120-tick simulations: five policies × eight starting/production",
             "conditions × five peer behaviors. These are constructed scenarios, not estimates",
             "of real-game survival probability or a replay of classmates' decisions.", "",
             "## Compared policies", "",
             "- Inactive: no actions, including no acceptance of gifts.",
             "- Current: seek imported resources below 5 upkeep ticks; pay at most 2 units.",
             "- Earlier only: seek below 10 ticks; pay at most 2 units.",
             "- Larger only: seek below 5 ticks; pay at most 5 units.",
             "- Both: seek below 10 ticks; pay at most 5 units.", "",
             "All active variants keep the same spending floors: 3 upkeep ticks for the",
             "production specialty, 5 for other resources. Live defaults have not changed.", "",
             "## Results", "",
             "| Policy | Target survived / 40 | Sum of shortage ticks while alive | Delivered gift units |",
             "| --- | ---: | ---: | ---: |"]
    for policy in dict.fromkeys(r['policy'] for r in results):
        group = [r for r in results if r['policy'] == policy]
        lines.append(f"| {policy} | {sum(r['survived'] for r in group)} / {len(group)} | "
                     f"{sum(r['shortage_ticks_before_failure'] for r in group)} | "
                     f"{sum(r['gifts_delivered'] for r in group)} |")
    lines += ["", "The shortage count includes the tick of failure but excludes subsequent ticks.",
              "Gift units measure delivered resources, not proof that those gifts saved a peer.", "",
              "## Conditions and peer behavior", "",
              "The recorded run supplies initial values, upkeep, production sequences, and public",
              "rules. The target is labeled P02 for continuity; its specialty is not hardcoded",
              "to water. Eight conditions are tested:", "",
              "1. Recorded start: (30, 30, 30).",
              "2. Low start: target inventory (8, 6, 6).",
              "3. Uneven start: target inventory (40, 2, 18).",
              "4. Abundant start: target inventory (50, 50, 50).",
              "5. Higher upkeep: target consumes (1, 2, 1) per tick.",
              "6. Production outage: target produces nothing during ticks 15–35 inclusive.",
              "7. Food specialty: rotate the whole world's resource labels once.",
              "8. Components specialty: rotate them twice.", "",
              "Five peer behaviors: immediate responses; one-tick delay; three-tick delay;",
              "no offers involving the target accepted; and immediate peers restricted to",
              "two-unit payments. Other peer profiles permit five-unit payments. Peers use",
              "the current strategy with five-tick seeking and remain unchanged across target",
              "policy comparisons. They have finite inventories, production, upkeep, and health.", "",
              "## Interpretation", "",
              "- All four active variants survived all eight conditions under immediate,",
              "  one-tick-delay, and small-trade-peer profiles, with no shortage ticks there.",
              "- All failed when nobody traded with the target or when all responses took",
              "  three ticks. Every variant currently expires offers after two ticks.",
              "- Earlier seeking and larger trades did not improve survival in this suite.",
              "  This does not establish that they have no benefit against other opponents.",
              "- The inactive recorded-start case reproduces failure at tick 40 and final",
              "  inventory (450, 0, 0), matching the historical P02 accounting.", "",
              "Retain current defaults based on this evidence. The next useful experiment",
              "is offer lifetime versus peer response time, followed by intermittent peers",
              "and peers that insist on larger lots. Do not claim an improvement merely",
              "because a proposed parameter sounds plausible.", "",
              "## Model boundaries", "",
              "This harness calls the real strategy decision function, not the network",
              "executor. It models production then upkeep, damage/recovery, permanent failure,",
              "offer expiry, finite-stock settlement, per-tick command limits, record budgets,",
              "and sequential decisions after state changes. Trading conserves each resource",
              "and cannot create negative inventory. Scheduling rotates deterministically.", "",
              "Historical production remains fixed even when our trades change the outcome;",
              "we do not infer whether a different real game would change production.",
              "Bots see only modeled public advertisements, their own inventory, and relevant",
              "offers—not the simulator's future production sequence or private peer stocks.",
              "Response delay is modeled by withholding incoming offers until the chosen tick.",
              "This is not a real server or classroom scheduling model. Full wire validation,",
              "latency within a tick, connection errors, readiness and retries are covered by",
              "separate client tests, not simulated here. Other planets are cooperative bots,",
              "not reconstructions of human teams. Results need more heterogeneous opponents.", "",
              "## Reproduce", "", "```sh", "python simulations/compare_p02.py", "```", "",
              "The JSON alongside this report contains all settings, per-run metrics, and",
              "per-tick target health and inventory. No live credentials or sockets are used."]
    path.write_text('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, default=ROOT/'data/run-2-log.json')
    parser.add_argument('--output', type=Path, default=ROOT/'run/simulations/policy-comparison.json')
    args = parser.parse_args()
    log = json.loads(args.log.read_text())
    results, timelines = compare(log)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_run=log['run_id'], results=results, timelines=timelines),indent=2)+'\n')
    write_report(results, args.output.with_suffix('.md'))
    print('Conditions | Scenario | P02 policy | Health | Failure tick | Trades | Gifts delivered | Survivors/9')
    for r in results:
        print(f"{r['conditions']} | {r['scenario']} | {r['policy']} | {r['final_health']} | {r['failure_tick']} | {r['trades']} | {r['gifts_delivered']} | {r['planets_survived']}")
    print(f'Results: {args.output}')


if __name__ == '__main__':
    main()
