"""Explicit project-scoped, read-only collection MCP; shared bounded stdio transport."""
import argparse
import json
from pathlib import Path
import sys
import time

from alden_abort import ABORT_STATE_NAME, AbortToken, AldenCancelled
from alden_collection import CollectionStore, read_action, require_collection_source_root
from alden_collection_retrieval import retrieve, verify_source_detail_context, SourceContextUnavailable
from alden_status_mcp import StdioServer, _check
from alden_gbrain_bridge import GbrainGraphReader
from alden_gbrain_client import GbrainError
from alden_gbrain_cache import ManagedGbrainReader
from alden_gbrain_import import FullGbrainReader


class CallToken(AbortToken):
    def __init__(self, path, control):
        super().__init__(path)
        self.control = control

    def is_cancelled(self):
        return super().is_cancelled() or self.control is not None and (
            self.control.stopped.is_set() or time.monotonic() >= self.control.deadline)


class SourceVersionChanged(Exception):
    """Fixed public failure; never carries a source body or a current version."""


class KnowledgeServer(StdioServer):
    server_name = 'alden-readonly-knowledge'

    def __init__(self, root, allowed_projects, *, tool_timeout=10, source_access=None, source_revision=None, source_context=None, source_relation_access=None, search_strategy='complete', source_factory=None, gbrain=None):
        super().__init__(None, tool_timeout=tool_timeout)
        self.root = Path(root)
        if gbrain is not None and not isinstance(gbrain, GbrainGraphReader):
            raise ValueError('knowledge_gbrain_config_invalid')
        self.gbrain = gbrain
        if source_factory is not None and (not callable(source_factory) or any(value is not None for value in (source_access, source_revision, source_context, source_relation_access))):
            raise ValueError('knowledge_source_factory_invalid')
        self.source_factory, self._factory_root = source_factory, self.root
        self.source_access, self.source_revision = source_access, source_revision
        if source_context is not None and not all(callable(value) for value in (source_access, source_revision, source_context)):
            raise ValueError('knowledge_source_context_authority_required')
        self.source_context = source_context
        self.source_relation_access = source_relation_access
        if search_strategy not in ('complete','indexed-lexical'):
            raise ValueError('knowledge_search_strategy_invalid')
        self.search_strategy=search_strategy
        self.allowed = frozenset(allowed_projects)
        if not self.allowed or len(self.allowed) > 16 or any(not isinstance(p, str) or not p or len(p) > 128 for p in self.allowed):
            raise ValueError('knowledge_startup_scope_required')
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise ValueError('knowledge_startup_path_invalid')
        if self.source_factory is not None:
            guards = self._source_guards(lambda: False)
            source_access, source_revision, source_context = guards['source_access'], guards['source_revision'], guards['source_context']
            self.source_access, self.source_revision, self.source_context = source_access, source_revision, source_context
            self.source_relation_access = guards['source_relation_access']
        require_collection_source_root(self.root, source_access, source_revision, source_context, self.source_relation_access)
        # Opening existing data validates paths/schema without creating or migrating it.
        self._collection_store_from_guards({'source_access':source_access,'source_revision':source_revision,
            'source_context':source_context,'source_relation_access':self.source_relation_access})

    def _collection_store_from_guards(self,guards):
        owner=getattr(guards['source_revision'],'__self__',None)
        stream=getattr(owner,'stream_revision',None)
        if stream is not None:
            if (not callable(stream) or getattr(stream,'__self__',None) is not owner
                    or any(value is not None and getattr(value,'__self__',None) is not owner for value in guards.values())):
                raise ValueError('knowledge_source_stream_authority_invalid')
        return CollectionStore.open_existing(self.root,source_access=guards['source_access'],
            source_revision=guards['source_revision'],source_relation_access=guards['source_relation_access'],source_stream=stream)

    def collection_store(self,cancelled=lambda:False):
        """Fresh bound read-only store, retaining a declared stream callback."""
        return self._collection_store_from_guards(self._source_guards(cancelled))

    def _source_guards(self, cancelled):
        if self.source_factory is None:
            return {'source_access': self.source_access, 'source_revision': self.source_revision, 'source_context': self.source_context,
                    'source_relation_access': self.source_relation_access}
        if self.root != self._factory_root:
            raise ValueError('knowledge_source_factory_root_changed')
        authority = self.source_factory(self.root, cancelled)
        if not callable(getattr(authority, 'collection_source_root', None)):
            raise ValueError('knowledge_source_factory_authority_invalid')
        result = {key: getattr(authority, method, None) for key, method in
                  [('source_access', 'access'), ('source_revision', 'revision'), ('source_context', 'retrieval_context')]}
        if any(not callable(value) or getattr(value, '__self__', None) is not authority for value in result.values()):
            raise ValueError('knowledge_source_factory_authority_invalid')
        relation = getattr(authority, 'relation_access', None)
        if relation is not None and (not callable(relation) or getattr(relation, '__self__', None) is not authority):
            raise ValueError('knowledge_source_factory_authority_invalid')
        result['source_relation_access'] = relation
        require_collection_source_root(self.root, *result.values())
        return result

    def tool_catalog(self):
        common = {'projects': {'type': 'array', 'minItems': 1, 'maxItems': 16,
                              'uniqueItems': True, 'items': {'type': 'string', 'enum': sorted(self.allowed)}},
                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}
        result = []
        for name, extras, description in [
            ('alden_knowledge_search', {'query': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
             'Retrieve retained versions and explicit relations with exact provenance. Read-only; explicitly supplied source authority is rechecked.'),
            ('alden_knowledge_graph', {'focus': {'type': 'string', 'maxLength': 256}},
             'Read bounded stored graph nodes and explicit relationships in the allowed project scope.'),
            ('alden_knowledge_history', {'after': {'type': 'integer', 'minimum': 0}},
             'Read persisted collection stages after a cursor; reading does not create activity.')]:
            result.append({'name': name, 'description': description,
                           'inputSchema': {'type': 'object', 'properties': {**common, **extras},
                                           'required': ['projects'] + (['query'] if name.endswith('search') else []),
                                           'additionalProperties': False},
                           'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                                           'idempotentHint': True, 'openWorldHint': False}})
            if name.endswith('search'):
                result[-1]['inputSchema']['properties']['limit'] = {'type': 'integer', 'minimum': 1, 'maximum': 10}
                if self.search_strategy=='indexed-lexical':
                    result[-1]['description'] += ' Bounded lexical candidates from stored-version text; not exhaustive or archived-only-text search.'
        result.append({'name': 'alden_knowledge_source',
                       'description': 'Read version-bound retained source detail in an allowed project. '
                                      'Use id and source_version from graph, or entity_id and source_version from search provenance. '
                                      'Rejects stale versions. Body text follows the disclosed text_projection; '
                                      'the first 12,000 characters carry an explicit truncated flag and archive hash. Read-only.',
                       'inputSchema': {'type': 'object', 'properties': {
                           'projects': common['projects'],
                           'node_id': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                           'expected_version': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
                           'required': ['projects', 'node_id', 'expected_version'], 'additionalProperties': False},
                       'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                                       'idempotentHint': True, 'openWorldHint': False}})
        if self.gbrain is not None:
            result.append({'name': 'alden_knowledge_paths',
                'description': 'Read GBrain paths bound to current Alden IDs, versions and source permissions. '
                               'Completeness covers one explicitly provisioned bounded snapshot. A stale cache is an error.',
                'inputSchema': {'type': 'object', 'properties': {
                    'projects': common['projects'],
                    'focus': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                    'depth': {'type': 'integer', 'minimum': 1, 'maximum': 3},
                    'direction': {'type': 'string', 'enum': ['in', 'out', 'both']}},
                    'required': ['projects', 'focus'], 'additionalProperties': False},
                'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                                'idempotentHint': True, 'openWorldHint': False}})
        return result

    def valid_call(self, params):
        name, args = params.get('name'), params.get('arguments')
        options = {'alden_knowledge_search': {'query'}, 'alden_knowledge_graph': {'focus'},
                   'alden_knowledge_history': {'after'},
                   'alden_knowledge_source': {'node_id', 'expected_version'}}
        if self.gbrain is not None:
            options['alden_knowledge_paths'] = {'focus', 'depth', 'direction'}
        common = {'projects'} if name in ('alden_knowledge_source', 'alden_knowledge_paths') else {'projects', 'limit'}
        if not isinstance(name, str) or name not in options or not isinstance(args, dict) or set(args) - (common | options[name]):
            return False
        projects = args.get('projects')
        if not isinstance(projects, list) or not 1 <= len(projects) <= 16 or any(not isinstance(p, str) or p not in self.allowed for p in projects):
            return False
        if len(set(projects)) != len(projects):
            return False
        if name == 'alden_knowledge_source':
            return all(isinstance(value, str) and value.strip() and len(value) <= 256
                       for value in (args.get('node_id'), args.get('expected_version')))
        if name == 'alden_knowledge_paths':
            return (isinstance(args.get('focus'), str) and 1 <= len(args['focus']) <= 256
                    and type(args.get('depth', 2)) is int and 1 <= args.get('depth', 2) <= 3
                    and args.get('direction', 'both') in ('in', 'out', 'both'))
        limit = args.get('limit', 6 if name.endswith('search') else 50)
        if type(limit) is not int or not 1 <= limit <= (10 if name.endswith('search') else 50):
            return False
        if name.endswith('search'):
            return isinstance(args.get('query'), str) and 1 <= len(args['query'].strip()) <= 256
        if name.endswith('graph'):
            return 'focus' not in args or isinstance(args['focus'], str) and 1 <= len(args['focus']) <= 256
        return type(args.get('after', 0)) is int and args.get('after', 0) >= 0

    def call_tool(self, params, control):
        _check(control)
        args, name = params['arguments'], params['name']
        token = CallToken(self.root / ABORT_STATE_NAME, control)
        def check():
            # Keep client cancellation/deadline distinct from the global epoch.
            _check(control)
            token.raise_if_cancelled()
        check()
        cancelled = lambda: token.is_cancelled() or control is not None and control.stopped.is_set()
        guards = self._source_guards(cancelled)
        check()
        try:
            if name.endswith('search'):
                import auto_reply_knowledge_graph as kg
                with kg.embedding_abort_scope(token):
                    result = retrieve(self.root, args['query'], projects=args['projects'],
                                      max_entities=args.get('limit', 6), cancelled=cancelled,
                                      **guards, search_strategy=self.search_strategy)
            elif name == 'alden_knowledge_paths':
                def current_store():
                    check()
                    return self.collection_store(cancelled)
                def current_graph():
                    check()
                    current_guards = self._source_guards(cancelled)
                    return read_action(self.root, 'collection-graph',
                        json.dumps({'projects': args['projects'], 'limit': 50}), cancelled=cancelled,
                        source_access=current_guards['source_access'], source_revision=current_guards['source_revision'],
                        source_relation_access=current_guards['source_relation_access'])
                authority = {'store_factory':current_store} if isinstance(self.gbrain,FullGbrainReader) else {'current':current_graph}
                result = self.gbrain.query(projects=args['projects'], focus=args['focus'],
                    **authority, check=check, depth=args.get('depth', 2),direction=args.get('direction', 'both'))
            else:
                source_read = name == 'alden_knowledge_source'
                action = 'collection-graph' if source_read or name.endswith('graph') else 'collection-history'
                if source_read:
                    options = {'projects': args['projects'], 'limit': 1, 'focus': args['node_id'],
                               'expected_version': args['expected_version'], 'details': True, 'hops': 0}
                else:
                    options = {'projects': args['projects'], 'limit': args.get('limit', 50)}
                    if 'after' in args:
                        options['after'] = args['after']
                    if 'focus' in args:
                        options['focus'], options['hops'] = args['focus'], 1
                result = read_action(self.root, action, json.dumps(options), cancelled=cancelled,
                                     source_access=guards['source_access'], source_revision=guards['source_revision'],
                                     source_relation_access=guards['source_relation_access'])
                if source_read:
                    if result.get('error') == 'collection_graph_version_changed':
                        raise SourceVersionChanged()
                    details = result.get('details')
                    if (result.get('ok') is not True or not isinstance(details, dict)
                            or details.get('basis') != 'source_record' or details.get('node_id') != args['node_id']
                            or details.get('version') != args['expected_version']):
                        raise ValueError('knowledge_source_binding_unavailable')
                    verify_source_detail_context(self.root, details, projects=args['projects'],
                                                 cancelled=cancelled, **guards)
                    result = {'ok': True, 'details': details}
                elif action == 'collection-history':
                    # The UI catalog is global; MCP exposes only this request's projects.
                    requested = frozenset(args['projects'])
                    result = {**result, 'projects': [project for project in result['projects']
                                                    if project['project'] in requested]}
        except Exception:
            check()
            raise
        check()
        return result

    def tool_failure_code(self, error):
        if isinstance(error, AldenCancelled):
            return 'alden_global_abort'
        if isinstance(error, SourceVersionChanged):
            return 'alden_source_version_changed'
        if isinstance(error, SourceContextUnavailable):
            return 'alden_source_context_unavailable'
        if isinstance(error, GbrainError):
            if str(error) in ('gbrain_projection_stale', 'gbrain_projection_incomplete'):
                return 'alden_gbrain_refresh_required'
            return 'alden_gbrain_unavailable'
        return super().tool_failure_code(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--allow-project', action='append', required=True)
    parser.add_argument('--search-strategy', choices=('complete','indexed-lexical'), default='complete')
    parser.add_argument('--messenger-source', choices=('kakaotalk','enrolled'))
    for name in ('root', 'receipt', 'home', 'manifest'):
        parser.add_argument('--gbrain-' + name, type=Path)
    parser.add_argument('--gbrain-managed-root', type=Path)
    parser.add_argument('--gbrain-full-root', type=Path)
    args = parser.parse_args()
    paths = [getattr(args, 'gbrain_' + name) for name in ('root', 'receipt', 'home', 'manifest')]
    if any(paths) and not all(paths):
        parser.error('all four --gbrain-* paths are required together')
    if sum((bool(args.gbrain_managed_root),bool(args.gbrain_full_root),any(paths)))>1:
        parser.error('bounded managed root, full root and unmanaged paths are mutually exclusive')
    try:
        factory = None
        if args.messenger_source:
            from alden_messenger_factory import open_existing_kakao, open_enrolled_messengers
            factory = open_enrolled_messengers if args.messenger_source=='enrolled' else open_existing_kakao
        gbrain = GbrainGraphReader(owned_root=paths[0], receipt=paths[1], home=paths[2], manifest=paths[3]) if all(paths) else None
        if args.gbrain_managed_root:
            gbrain = ManagedGbrainReader(args.gbrain_managed_root, collection_root=args.state_root, projects=args.allow_project)
        if args.gbrain_full_root:
            gbrain = FullGbrainReader(args.gbrain_full_root,collection_root=args.state_root,projects=args.allow_project)
        KnowledgeServer(args.state_root, args.allow_project, search_strategy=args.search_strategy,
                        source_factory=factory, gbrain=gbrain).serve(sys.stdin.buffer, sys.stdout.buffer)
    except (BrokenPipeError, KeyboardInterrupt):
        pass


if __name__ == '__main__':
    main()
