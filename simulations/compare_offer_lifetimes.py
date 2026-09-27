"""Vary only the target's offer lifetime; all other policies remain fixed."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from compare_p02 import ROOT, Simulation, Scenario, Policy

CONDITIONS = ('recorded', 'low_start', 'uneven_start', 'abundant_start',
              'higher_upkeep', 'production_outage', 'food_specialty', 'components_specialty')
SCENARIOS = (Scenario('responsive'), Scenario('one_tick_delay', delay=1),
             Scenario('three_tick_delay', delay=3), Scenario('ignore_P02', ignore_target=True),
             Scenario('small_trade_peers', peer_trade_size=2))


def report(results, path):
    lines = ['# Offer lifetime comparison', '',
             '160 deterministic 120-turn simulations: four target offer lifetimes ×',
             'eight conditions × five peer behaviors. This is a controlled model, not',
             'a real-game survival probability.', '',
             '## Controlled comparison', '',
             'Only the target offer lifetime changes: 2, 4, 6, or 12 turns, capped by',
             'the server limit. Advertisements stay at two turns, as do peer offers.',
             'Reserve floors, seeking thresholds, payment caps, and gifts are unchanged.',
             'Other planets retain the same modeled policies for every comparison.',
             'Live defaults remain unchanged at two turns.', '',
             'Conditions and model assumptions are described in the checked-in docs/analysis/policy-comparison.md report.',
             'Incoming offers are processed after the scenario delay; expiration occurs first',
             'at the expiry tick. Long offers still reserve their full promised quantities',
             'in the strategy and block another outgoing offer to the same peer.', '',
             '## Results', '',
             '| Offer lifetime | Target survived / 40 | Shortage turns while alive | Expired offers | Delivered gift units |',
             '| --- | ---: | ---: | ---: | ---: |']
    for ttl in (2, 4, 6, 12):
        rows = [r for r in results if r['offer_ttl'] == ttl]
        lines.append(f"| {ttl} | {sum(r['survived'] for r in rows)} / {len(rows)} | "
                     f"{sum(r['shortage_ticks_before_failure'] for r in rows)} | "
                     f"{sum(r['expired_offers'] for r in rows)} | "
                     f"{sum(r['gifts_delivered'] for r in rows)} |")
    lines += ['', '## Survival by response pattern', '',
              '| Peer behavior | 2 turns | 4 turns | 6 turns | 12 turns |',
              '| --- | ---: | ---: | ---: | ---: |']
    for scenario in SCENARIOS:
        values = []
        for ttl in (2,4,6,12):
            rows = [r for r in results if r['scenario'] == scenario.name and r['offer_ttl'] == ttl]
            values.append(f"{sum(r['survived'] for r in rows)} / {len(rows)}")
        lines.append('| ' + scenario.name + ' | ' + ' | '.join(values) + ' |')
    lines += ['', '## Paired changes from the two-turn baseline', '',
              '| Lifetime | Improved from failure to survival | Regressed from survival to failure |',
              '| --- | ---: | ---: |']
    baseline = {(r['conditions'],r['scenario']):r for r in results if r['offer_ttl'] == 2}
    for ttl in (4,6,12):
        rows = [r for r in results if r['offer_ttl'] == ttl]
        improved = sum(r['survived'] and not baseline[r['conditions'],r['scenario']]['survived'] for r in rows)
        regressed = sum(not r['survived'] and baseline[r['conditions'],r['scenario']]['survived'] for r in rows)
        lines.append(f'| {ttl} | {improved} | {regressed} |')
    lines += ['', '## Interpretation limits', '',
              'A longer lifetime allows a delayed acceptance; it cannot force a peer to trade.',
              'A single long-lived offer can also tie up a trading partner while supplies fall.',
              'The three-turn-delay scenario applies to every planet. Peers still issue',
              'two-turn offers, so peer-to-peer exchanges can fail even if target offers last',
              'longer. This deliberately tests a unilateral change, not coordinated adoption.',
              'Stock, health, production, and peer behavior remain modeled assumptions.',
              'Fewer expirations alone do not establish better survival or cooperation.', '',
              '## Reproduce', '', '```sh', 'python simulations/compare_offer_lifetimes.py', '```', '',
              'The JSON alongside this report includes per-run policy settings and trajectories.',
              'No live credentials, sockets, or live trades are used.']
    path.write_text('\n'.join(lines)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,default=ROOT/'data/run-2-log.json')
    parser.add_argument('--output',type=Path,default=ROOT/'run/simulations/offer-lifetimes.json')
    args = parser.parse_args()
    log = json.loads(args.log.read_text())
    results, timelines = [], {}
    for condition in CONDITIONS:
        for scenario in SCENARIOS:
            for ttl in (2,4,6,12):
                policy = Policy(offer_ttl=ttl)
                sim = Simulation(log,policy,scenario,condition)
                result = sim.run()
                result.update(offer_ttl=ttl,settings=asdict(policy))
                results.append(result)
                timelines[f'{condition}/{scenario.name}/{ttl}'] = sim.timeline
        print('Completed:',condition,flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_run=log['run_id'],results=results,timelines=timelines),indent=2)+'\n')
    report(results,args.output.with_suffix('.md'))
    for ttl in (2,4,6,12):
        rows = [r for r in results if r['offer_ttl']==ttl]
        print(ttl,'turns:',sum(r['survived'] for r in rows),'/',len(rows),'survived')
    print('Report:',args.output.with_suffix('.md'))


if __name__ == '__main__':
    main()
