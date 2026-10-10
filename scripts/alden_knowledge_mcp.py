"""Explicit project-scoped, read-only collection MCP; shared bounded stdio transport."""
import argparse
import json
from pathlib import Path
import sys
import time

from alden_abort import ABORT_STATE_NAME, AbortToken
from alden_collection import CollectionStore, read_action
from alden_collection_retrieval import retrieve, trace_document
from alden_status_mcp import StdioServer, _check


class CallToken(AbortToken):
    def __init__(self, path, control):
        super().__init__(path)
        self.control = control

    def is_cancelled(self):
        return super().is_cancelled() or self.control is not None and (
            self.control.stopped.is_set() or time.monotonic() >= self.control.deadline)


class KnowledgeServer(StdioServer):
    server_name = 'alden-readonly-knowledge'

    def __init__(self, root, allowed_projects, *, tool_timeout=10):
        super().__init__(None, tool_timeout=tool_timeout)
        self.root = Path(root)
        self.allowed = frozenset(allowed_projects)
        if not self.allowed or len(self.allowed) > 16 or any(not isinstance(p, str) or not p or len(p) > 128 for p in self.allowed):
            raise ValueError('knowledge_startup_scope_required')
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise ValueError('knowledge_startup_path_invalid')
        # Opening existing data validates paths/schema without creating or migrating it.
        CollectionStore.open_existing(self.root)

    def tool_catalog(self):
        common = {'projects': {'type': 'array', 'minItems': 1, 'maxItems': 16,
                              'uniqueItems': True, 'items': {'type': 'string', 'enum': sorted(self.allowed)}},
                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}
        result = []
        for name, extras, description in [
            ('alden_knowledge_search', {'query': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                                       'target_id': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
             'Retrieve retained collection versions and explicit relations with exact provenance. No writes, model loads or account access.'),
            ('alden_knowledge_graph', {'focus': {'type': 'string', 'maxLength': 256}},
             'Read bounded stored graph nodes and explicit relationships in the allowed project scope.'),
            ('alden_knowledge_history', {'after': {'type': 'integer', 'minimum': 0}},
             'Read persisted collection stages after a cursor; reading does not create activity.'),
            ('alden_knowledge_trace', {
                'document_id': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                'expected_version': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                'target_id': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
             'Read a scoped, hash-verified source-to-storage/FTS/dense/graph receipt. Never runs an LLM or writes.')]:
            result.append({'name': name, 'description': description,
                           'inputSchema': {'type': 'object', 'properties': {**common, **extras},
                                           'required': ['projects'] + (['query'] if name.endswith('search') else ['document_id'] if name.endswith('trace') else []),
                                           'additionalProperties': False},
                           'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                                           'idempotentHint': True, 'openWorldHint': False}})
            if name.endswith('search'):
                result[-1]['inputSchema']['properties']['limit'] = {'type': 'integer', 'minimum': 1, 'maximum': 10}
            if name.endswith('trace'):
                result[-1]['inputSchema']['properties'].pop('limit')
        return result

    def valid_call(self, params):
        name, args = params.get('name'), params.get('arguments')
        options = {'alden_knowledge_search': {'query','target_id'}, 'alden_knowledge_graph': {'focus'},
                   'alden_knowledge_history': {'after'},
                   'alden_knowledge_trace': {'document_id','expected_version','target_id'}}
        if not isinstance(name, str) or name not in options or not isinstance(args, dict) or set(args) - ({'projects', 'limit'} | options[name]):
            return False
        projects = args.get('projects')
        if not isinstance(projects, list) or not 1 <= len(projects) <= 16 or any(not isinstance(p, str) or p not in self.allowed for p in projects):
            return False
        if len(set(projects)) != len(projects):
            return False
        limit = args.get('limit', 6 if name.endswith('search') else 50)
        if type(limit) is not int or not 1 <= limit <= (10 if name.endswith('search') else 50):
            return False
        if name.endswith('search'):
            return (isinstance(args.get('query'), str) and 1 <= len(args['query'].strip()) <= 256
                    and ('target_id' not in args or isinstance(args['target_id'],str)
                         and 1 <= len(args['target_id']) <= 256))
        if name.endswith('trace'):
            return (isinstance(args.get('document_id'), str) and 1 <= len(args['document_id']) <= 256
                    and all(key not in args or isinstance(args[key], str) and 1 <= len(args[key]) <= 256
                            for key in ('expected_version','target_id')) and 'limit' not in args)
        if name.endswith('graph'):
            return 'focus' not in args or isinstance(args['focus'], str) and 1 <= len(args['focus']) <= 256
        return type(args.get('after', 0)) is int and args.get('after', 0) >= 0

    def call_tool(self, params, control):
        _check(control)
        args, name = params['arguments'], params['name']
        token = CallToken(self.root / ABORT_STATE_NAME, control)
        cancelled = lambda: token.is_cancelled() or control is not None and control.stopped.is_set()
        if name.endswith('search'):
            import auto_reply_knowledge_graph as kg
            with kg.embedding_abort_scope(token):
                result = retrieve(self.root, args['query'], projects=args['projects'],
                                  max_entities=args.get('limit', 6), cancelled=cancelled,
                                  target_id=args.get('target_id'))
        elif name.endswith('trace'):
            result = trace_document(self.root, args['document_id'], projects=args['projects'],
                                    target_id=args.get('target_id'), expected_version=args.get('expected_version'))
        else:
            action = 'collection-graph' if name.endswith('graph') else 'collection-history'
            options = {'projects': args['projects'], 'limit': args.get('limit', 50)}
            if 'after' in args:
                options['after'] = args['after']
            if 'focus' in args:
                options['focus'], options['hops'] = args['focus'], 1
            result = read_action(self.root, action, json.dumps(options))
        _check(control)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--allow-project', action='append', required=True)
    args = parser.parse_args()
    try:
        KnowledgeServer(args.state_root, args.allow_project).serve(sys.stdin.buffer, sys.stdout.buffer)
    except (BrokenPipeError, KeyboardInterrupt):
        pass


if __name__ == '__main__':
    main()
