"""Summarize a client JSONL history without changing it."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(path):
    first = last = None
    actions, results, waits, errors = Counter(), Counter(), Counter(), Counter()
    snapshots = 0
    for number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(f'Warning: skipped incomplete or invalid JSON on line {number}.')
            continue
        if entry['event'] == 'connection_error':
            errors[str(entry.get('http_status') or entry.get('error_type'))] += 1
        if entry['event'] == 'decision' and entry.get('action') is None:
            waits[entry['reason']] += 1
        if entry['event'] != 'message':
            continue
        message = entry['message']
        if entry['direction'] == 'sent':
            actions.update(message.keys())
        elif 'state' in message:
            last = message['state']
            first = first or last
            snapshots += 1
        elif 'result' in message:
            results[message['result']['code']] += 1
        elif 'protocol_error' in message:
            errors[message['protocol_error']['code']] += 1
    print('Sent messages:', dict(actions))
    print('Command results:', dict(results))
    print('Errors:', dict(errors))
    print('Wait/skip reasons:', dict(waits))
    if last:
        print('Run:', last['run_id'], 'Station:', last['self_station_id'])
        print('Snapshots:', snapshots, 'Ticks:', first['tick'], 'to', last['tick'])
        print('Latest phase:', last['phase'])
        for label, state in [('First', first), ('Latest', last)]:
            me = state['self']
            print(label, 'health:', me['health'], 'inventory:', me['inventory'])
        print('Latest failed_once:', last['self']['failed_once'])
        print('Latest shortage_ticks:', last['self']['shortage_ticks'])
    else:
        print('No state received; a successful game connection is not established by this log.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    summarize(parser.parse_args().log)
