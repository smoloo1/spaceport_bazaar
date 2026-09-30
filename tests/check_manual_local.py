"""Run against a temporary local server: python tests/check_manual_local.py."""
import asyncio
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client'))
import bazaar
from manual import ManualSession


async def check(url, credentials):
    # Exercise actual stdin handling and clean shutdown before the scripted trades.
    env = os.environ.copy()
    env['BAZAAR_TOKEN'] = 'intentionally-wrong-live-token'
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-u', str(ROOT / 'client/run_live.py'), '--practice',
        '--interactive', '--url', url, '--credentials', str(credentials),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env)
    async def until(marker):
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), 5)
            if not line:
                raise AssertionError('Interactive process ended unexpectedly')
            if marker in line:
                return
    try:
        await until(b'Trading commands accept')
        proc.stdin.write(b'ready\n')
        await proc.stdin.drain()
        await until(b'Readiness confirmed.')
        proc.stdin.write(b'quit\n')
        await proc.stdin.drain()
        await asyncio.wait_for(proc.wait(), 5)
        assert proc.returncode == 0, (await proc.stderr.read()).decode()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    print('PASS: interactive readiness, stdin, quit, and practice credentials overriding unrelated live token')

    async with bazaar.BazaarClient(url, bazaar.load_token(credentials)) as client:
        session = ManualSession(client)
        async def receive(kind):
            msg = await client.recv()
            assert msg.WhichOneof('message') == kind
            session.receive(msg)
            return getattr(msg, kind)
        async def trade(command):
            await session.command(command)
            result = await receive('result')
            assert result.ok
            await receive('state')
            return bazaar.nullable_value(result.object_id)
        await receive('state')
        await session.command('ready')
        await receive('readiness')
        await trade('advertise water food 6 --request-id student-advertise-1')
        ad_id = await trade('advertise - components 6 --request-id student-advertise-seeking-1')
        await trade('offer P02 2,0,0 0,1,0 6 --request-id student-offer-1')
        await receive('state')
        await receive('state')
        gift = next(o for o in session.state.offers.items if o.proposer_id == 'P02')
        await trade(f'accept {gift.offer_id} --request-id student-accept-1')
        await trade(f'withdraw {ad_id} --request-id student-withdraw-1')
        await session.command('advertise water food 6 --request-id student-advertise-2')
        await receive('protocol_error')
        await session.command('sync')
        await receive('state')
        assert bazaar.as_tuple(session.state.self.inventory) == (28, 31, 31)
        assert (client.sent, client.received) == (8, 16)
    print('PASS: all 10 README steps through manual command parsing')


def main():
    with tempfile.TemporaryDirectory(prefix='bazaar-manual-') as temp:
        temp = Path(temp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        arch = 'arm64' if platform.machine() in ('aarch64', 'arm64') else 'x86_64'
        binary = ROOT / f'artifacts/bazaar-protobuf-starter-linux/spaceport-validate-linux-{arch}'
        with (temp / 'server.log').open('w') as log:
            server = subprocess.Popen([str(binary), '--codec', 'protobuf', '--addr', f'127.0.0.1:{port}'],
                                      cwd=temp, stdout=log, stderr=log)
            try:
                credentials = temp / 'validation-credentials.json'
                for _ in range(100):
                    if credentials.exists():
                        break
                    time.sleep(.05)
                asyncio.run(check(f'ws://127.0.0.1:{port}/ws', credentials))
                report = json.loads((temp / 'validation-report.json').read_text())
                assert report['status'] == 'sample exchange completed'
                assert report['last_completed_step'] == 10
                print('PASS: server report confirms completion')
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    main()
