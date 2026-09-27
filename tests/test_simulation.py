import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'simulations'))
from compare_p02 import Simulation, Scenario
from strategy import Action


def fixture():
    names = {'P02': 'water', 'P06': 'food', 'P01': 'components'}
    resources = ('water', 'food', 'components')
    history = []
    for tick in range(121):
        history.append(dict(tick=tick, stations=[dict(display_id=sid,
            initial_inventory=dict.fromkeys(resources, 30),
            upkeep_per_tick=dict.fromkeys(resources, 1), health=100,
            last_production={r: (4 if tick <= 60 else 5) if r == specialty and tick else 0
                             for r in resources}) for sid, specialty in names.items()]))
    return dict(history=history, snapshot=dict(
        rules=dict(duration_ticks=120,max_health=100,shortage_damage_per_unit=5,
                   recovery_per_fully_supplied_tick=5,new_commands_per_station_per_tick=10,
                   max_request_records_per_station=2048,max_command_bytes=16384,
                   max_offer_ttl_ticks=12,max_publication_ttl_ticks=12,max_open_outgoing_offers=24),
        stations=[dict(display_id=sid,station_id=sid) for sid in names],
        economic=dict(stations=[dict(display_id=sid,produced_total={r:540 if r == specialty else 0
                                                                  for r in resources})
                                for sid,specialty in names.items()])))


class SimulationTests(unittest.TestCase):
    def test_inactive_baseline_matches_known_resource_accounting(self):
        sim = Simulation(fixture(), None, Scenario('test'))
        result = sim.run()
        self.assertEqual(result['failure_tick'], 40)
        self.assertEqual(result['final_inventory'], [450, 0, 0])
        self.assertEqual(result['trades'], 0)

    def test_settlement_conserves_resources_and_counts_delivered_gift(self):
        sim = Simulation(fixture(), None, Scenario('test'))
        action = Action('offer', ('P06', (1,0,0),(0,0,0),2), '', ())
        sim.execute('P02', action)
        self.assertEqual(sim.nodes['P02']['gifts_sent'], 0)
        sim.execute('P06', Action('accept', (sim.offers[0].offer_id,), '', ()))
        self.assertEqual(sim.nodes['P02']['gifts_sent'], 1)
        self.assertEqual(sim.nodes['P02']['inventory'], [29,30,30])
        self.assertEqual(sim.nodes['P06']['inventory'], [31,30,30])
        self.assertEqual(sim.initial_target['inventory'], [30,30,30])

    def test_delay_hides_incoming_but_keeps_outgoing_commitment_visible(self):
        sim = Simulation(fixture(), None, Scenario('test',delay=3))
        sim.execute('P02', Action('offer', ('P06',(1,0,0),(0,1,0),2),'',()))
        self.assertEqual(len(sim.state('P02').offers.items), 1)
        self.assertEqual(len(sim.state('P06').offers.items), 0)
        sim.tick = 3
        self.assertEqual(len(sim.state('P06').offers.items), 1)

    def test_specialty_rotation_and_outage_are_explicit_inputs(self):
        sim = Simulation(fixture(), None, Scenario('test'), 'food_specialty')
        self.assertEqual(sim.nodes['P02']['specialty'], 2)
        self.assertEqual(sim.production[1]['P02'], [0,4,0])
        sim = Simulation(fixture(), None, Scenario('test'), 'production_outage')
        self.assertEqual(sim.production[15]['P02'], [0,0,0])
        self.assertEqual(sim.production[36]['P02'], [4,0,0])


if __name__ == '__main__':
    unittest.main()
