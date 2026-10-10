"""Native host reacquisition for an explicitly enabled existing video target.

One owned CLI client, three native reads, no agent/model/tab/account discovery.
Cancelling the client discards its result; the host may finish its read remotely.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time

from alden_abort import AldenCancelled
from alden_collect import input_destination, json_snapshot, receive_input, youtube_video_snapshot
from alden_collection import safe_directory

PROVIDER = 'aside-youtube'
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 256 * 1024


def supported(target):
    return (target['platform'] == 'youtube' and target['kind'] == 'video'
            and target['config'].get('adapter') == 'youtube-video'
            and isinstance(target['original_id'], str)
            and re.fullmatch(r'[A-Za-z0-9_-]{11}', target['original_id']) is not None)


def host_cli():
    path = Path.home() / '.aside/cli/Aside CLI.app/Contents/MacOS/aside'
    try:
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError()
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or not os.access(path, os.X_OK):
            raise ValueError()
    except (OSError, ValueError) as error:
        raise RuntimeError('collection_acquisition_host_unavailable') from error
    return path


def _stop(child):
    if child.poll() is None:
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=1)


def _native_read(code, *, cancelled, timeout):
    if cancelled(): raise AldenCancelled('collection_acquisition_cancelled')
    command = [str(host_cli()), 'repl', '--host', 'local', '--account', 'u0', code]
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
    except OSError as error:
        raise RuntimeError('collection_acquisition_host_unavailable') from error
    deadline = time.monotonic() + timeout
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                if cancelled(): raise AldenCancelled('collection_acquisition_cancelled')
                if time.monotonic() >= deadline: raise RuntimeError('collection_acquisition_timeout')
                for key, _ in selector.select(.05):
                    raw = os.read(key.fileobj.fileno(), 65536)
                    if not raw: selector.unregister(key.fileobj); continue
                    chunks[key.data].extend(raw)
                    if len(chunks[key.data]) > (MAX_OUTPUT_BYTES if key.data == 'stdout' else MAX_ERROR_BYTES):
                        raise RuntimeError('collection_acquisition_output_budget')
        try: child.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error: raise RuntimeError('collection_acquisition_timeout') from error
        if child.returncode: raise RuntimeError('collection_acquisition_host_failed')
        return bytes(chunks['stdout'])
    finally:
        _stop(child)
        child.stdout.close(); child.stderr.close()


def _decode(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('duplicate')
            value[key] = item
        return value
    def finite(_): raise ValueError('nonfinite')
    try:
        text = raw.decode('utf-8').lstrip()
        value, end = json.JSONDecoder(object_pairs_hook=unique, parse_constant=finite).raw_decode(text)
        trailer = re.sub(r'\x1b\[[0-9;]*m', '', text[end:]).strip()
        if trailer and not re.fullmatch(r'\[ok \| [0-9]+(?:\.[0-9]+)?ms\]', trailer): raise ValueError('trailer')
        if not isinstance(value, dict): raise ValueError('shape')
        return value
    except (UnicodeError, ValueError) as error:
        raise RuntimeError('collection_acquisition_response_invalid') from error


def native_code(video_id):
    # JSON encoding is JavaScript data encoding, never shell interpolation.
    return '''console.log(JSON.stringify(await (async () => {
      let stage = 'metadata';
      try {
        const metadata = await youtube.getMetadata(VIDEO_ID);
        const observedAt = new Date().toISOString();
        stage = 'tracks'; const tracks = await youtube.listTranscriptLanguages(VIDEO_ID);
        stage = 'captions';
        const text = await youtube.getTranscript(VIDEO_ID, {lang:'ko', includeTimestamp:true});
        return {ok:true,schema:1,host:'Aside',observedAt,captionObservedAt:new Date().toISOString(),
          acquisition:{provider:'aside-native-youtube',host:'local',account:'u0',nativeReads:3},
          videos:[{metadata,tracks,captions:{availability:'available',language:'ko',text}}]};
      } catch(error) {
        const match = String(error).match(/\\b(401|403|404|429|5[0-9]{2})\\b/);
        return {ok:false,stage,httpStatus:match ? Number(match[1]) : null};
      }
    })()));'''.replace('VIDEO_ID', json.dumps(video_id))


def acquire_input(store, target_id, policy, *, token, cancelled, timeout,
                  publication_guard=None, reader=_native_read):
    if cancelled(): raise AldenCancelled('collection_acquisition_cancelled')
    target = store.target(target_id)
    if not supported(target): raise ValueError('collection_acquisition_target_unsupported')
    if policy.get('provider') != PROVIDER or policy.get('enabled') is not True:
        raise ValueError('collection_acquisition_not_enabled')
    if set(policy)!={'provider','enabled','revision'} or type(policy.get('revision')) is not int or not 1<=policy['revision']<=9007199254740991:
        raise ValueError('collection_acquisition_policy_invalid')
    if not target['enabled'] or not any(p['permission'] != 'denied' for p in target['projects']):
        raise ValueError('collection_source_scope_denied')
    previous, source = json_snapshot(input_destination(store, target))
    youtube_video_snapshot(target, previous, source)
    raw = reader(native_code(target['original_id']), cancelled=cancelled, timeout=min(110, timeout))
    if cancelled(): raise AldenCancelled('collection_acquisition_cancelled')
    incoming = _decode(raw)
    if incoming.get('ok') is not True:
        stage = incoming.get('stage')
        if stage not in {'metadata', 'tracks', 'captions'}: raise RuntimeError('collection_acquisition_response_invalid')
        status = incoming.get('httpStatus')
        suffix = '_http_' + str(status) if type(status) is int and (status in {401,403,404,429} or 500<=status<=599) else '_unavailable'
        raise RuntimeError('collection_acquisition_' + stage + suffix)
    incoming.pop('ok')
    youtube_video_snapshot(target, incoming, {})
    if incoming.get('acquisition') != {'provider':'aside-native-youtube','host':'local','account':'u0','nativeReads':3}:
        raise RuntimeError('collection_acquisition_provenance_invalid')
    # Declared parent context is not acquired author identity.
    context = previous['videos'][0].get('declared_context')
    if context is not None: incoming['videos'][0]['declared_context'] = context
    folder = safe_directory(store.root / 'acquisition-staging')
    with tempfile.TemporaryDirectory(prefix='native-', dir=folder) as temporary:
        path = Path(temporary) / 'input.json'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write((json.dumps(incoming,ensure_ascii=False,indent=2)+'\n').encode())
            handle.flush(); os.fsync(handle.fileno())
        if cancelled(): raise AldenCancelled('collection_acquisition_cancelled')
        result = receive_input(store,target_id,path,source['sha256'],token=token,
                               publication_guard=publication_guard)
    return {**result,'provider':PROVIDER,'policy_revision':policy['revision'],
            'observed_at':incoming['observedAt'],'caption_observed_at':incoming['captionObservedAt'],
            'native_reads':3,'native_reads_this_attempt':3,
            'scope':'one enabled existing video; native metadata/tracks/captions, no channel survey'}
