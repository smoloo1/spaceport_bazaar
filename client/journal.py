"""Append-only JSONL run history. Never stores connection headers or credentials."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from google.protobuf.json_format import MessageToDict


class LogWriteError(RuntimeError):
    pass


class Journal:
    def __init__(self, directory, token):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        self.path = directory / f'client-{stamp}-{uuid.uuid4().hex[:8]}.jsonl'
        self._token = token
        self._file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        self._sequence = 0

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self._token, '[REDACTED]') if self._token else value
        if isinstance(value, dict):
            return {key: self._redact(v) for key, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [self._redact(v) for v in value]
        return value

    def record(self, event, **fields):
        self._sequence += 1
        entry = dict(schema_version=1, sequence=self._sequence,
                     timestamp=datetime.now(timezone.utc).isoformat(), event=event, **fields)
        try:
            self._file.write(json.dumps(self._redact(entry), ensure_ascii=False) + '\n')
            self._file.flush()
        except OSError as exc:
            raise LogWriteError('Could not write run history; stopping the client.') from exc

    def message(self, direction, msg):
        self.record('message', direction=direction,
                    message=MessageToDict(msg, preserving_proto_field_name=True))

    def close(self):
        try:
            self.record('session_end')
        finally:
            self._file.close()
