import contextlib
import io
import json
import tempfile
from pathlib import Path
import unittest
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))

import bazaar
from automated import AutomatedSession
from journal import Journal
from helpers import snapshot


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshots_decisions_and_token_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            token = 'fake-private-client-token'
            journal = Journal(directory, token)
            client = bazaar.BazaarClient('ws://localhost', token, journal=journal)
            client.send = AsyncMock()
            msg = snapshot()
            with contextlib.redirect_stdout(io.StringIO()):
                client._log('<-', msg)
                session = AutomatedSession(client, advisory=True)
                await session.receive(msg)
            journal.record('test', nested={'value': token})
            journal.close()
            raw = journal.path.read_text()
            self.assertNotIn(token, raw)
            records = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual([r['sequence'] for r in records], list(range(1,len(records)+1)))
            self.assertEqual(records[0]['message']['state']['self']['inventory']['water'], '10')
            decision = next(r for r in records if r['event'] == 'decision')
            self.assertEqual(decision['snapshot_sequence'], msg.state.snapshot_sequence)
            self.assertEqual(decision['action'], 'advertise')
            self.assertEqual(records[-1]['event'], 'session_end')
            self.assertEqual(journal.path.stat().st_mode & 0o777, 0o600)
            client.send.assert_not_awaited()

    async def test_separate_runs_never_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Journal(directory, 'secret'), Journal(directory, 'secret')
            first.close()
            second.close()
            self.assertNotEqual(first.path, second.path)
            self.assertTrue(first.path.exists() and second.path.exists())
