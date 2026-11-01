"""Derived GBrain pages and graph results bound to one current Alden snapshot."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json

from alden_gbrain_client import GbrainError, VERSION, _json


POLICY = 'alden-gbrain-scoped-reference-v2'
MAX_NODES = 2048
MAX_EDGES = 8192


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GbrainError('gbrain_snapshot_invalid') from None


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _text(value, maximum=256):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum
            or any(ord(c) < 32 for c in value)):
        raise GbrainError('gbrain_snapshot_invalid')
    return value


class GbrainProjection:
    def __init__(self, packet, projects):
        if (not isinstance(projects, list) or not 1 <= len(projects) <= 16
                or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects)
                or len(set(projects)) != len(projects)):
            raise GbrainError('gbrain_project_scope_invalid')
        raw = _encoded(packet)
        if len(raw) > 32 * 1024 * 1024:
            raise GbrainError('gbrain_snapshot_budget')
        packet = _json(raw)  # Detach caller-owned dictionaries before binding.
        self.projects = sorted(projects)
        if (not isinstance(packet, dict) or packet.get('ok') is not True
                or not isinstance(packet.get('scope'), list)
                or any(not isinstance(p, str) for p in packet['scope'])
                or sorted(packet['scope']) != self.projects
                or type(packet.get('revision')) is not int or packet['revision'] < 0):
            raise GbrainError('gbrain_snapshot_scope_mismatch')
        nodes, edges, targets = (packet.get(k) for k in ('nodes', 'edges', 'targets'))
        if (not isinstance(nodes, list) or len(nodes) > MAX_NODES
                or not isinstance(edges, list) or len(edges) > MAX_EDGES
                or not isinstance(targets, list) or len(targets) > 16384):
            raise GbrainError('gbrain_snapshot_shape_invalid')
        target_map = {}
        for target in targets:
            if not isinstance(target, dict):
                raise GbrainError('gbrain_snapshot_invalid')
            identity = _text(target.get('id'))
            if identity in target_map:
                raise GbrainError('gbrain_snapshot_duplicate_id')
            target_map[identity] = {'id': identity, 'label': _text(target.get('label'), 1024),
                                    'platform': _text(target.get('platform'), 64)}
        self.nodes, self.edges, source_nodes = {}, [], []
        memberships = []
        for node in nodes:
            if not isinstance(node, dict):
                raise GbrainError('gbrain_snapshot_invalid')
            identity, version, target = (_text(node.get(k)) for k in ('id', 'source_version', 'source_target'))
            if identity in self.nodes or target not in target_map:
                raise GbrainError('gbrain_snapshot_node_unbound')
            contexts = node.get('source_contexts')
            if not isinstance(contexts, list) or len(contexts) > 1024:
                raise GbrainError('gbrain_snapshot_context_invalid')
            clean_contexts = []
            for context in contexts:
                if (not isinstance(context, dict) or context.get('project') not in self.projects
                        or not isinstance(context.get('target_id'), str)
                        or context['target_id'] not in target_map):
                    raise GbrainError('gbrain_snapshot_context_invalid')
                clean_contexts.append({k: _text(context.get(k)) for k in ('project', 'target_id', 'version')})
            if not any(c['target_id'] == target and c['version'] == version for c in clean_contexts):
                raise GbrainError('gbrain_snapshot_node_unbound')
            clean = {'id': identity, 'label': _text(node.get('label'), 1024),
                     'category': _text(node.get('category'), 256), 'source_version': version,
                     'source_target': target, 'source_contexts': sorted(clean_contexts, key=_encoded)}
            self.nodes[identity] = clean
            source_nodes.append(clean)
            for current in clean_contexts:
                if current['version'] == version:
                    memberships.append((identity, current['target_id'], current))
        edge_ids = set()
        for edge in edges:
            if (not isinstance(edge, dict) or not isinstance(edge.get('source'), str)
                    or not isinstance(edge.get('target'), str) or edge['source'] not in self.nodes
                    or edge['target'] not in self.nodes):
                raise GbrainError('gbrain_snapshot_edge_unbound')
            identity = _text(edge.get('id'))
            if identity in edge_ids:
                raise GbrainError('gbrain_snapshot_duplicate_id')
            edge_ids.add(identity)
            proof = edge.get('reference_group')
            evidence = edge.get('evidence')
            if (not isinstance(proof, dict) or proof.get('kind') != 'directed-relation-group-v1'
                    or type(proof.get('count')) is not int or proof['count'] < 1
                    or not isinstance(evidence, dict) or evidence.get('retracted') is not False
                    or not isinstance(evidence.get('source_event_ids'), list)
                    or not 1 <= len(evidence['source_event_ids']) <= min(16, proof['count'])):
                raise GbrainError('gbrain_snapshot_edge_proof_invalid')
            _text(proof.get('revision'))
            for event in evidence['source_event_ids']:
                _text(event)
            self.edges.append({k: edge[k] for k in ('id', 'source', 'target', 'relation', 'reference_group', 'evidence')})
            _text(edge.get('relation'), 128)
        used_targets = {target for _, target, _ in memberships}
        binding = {'policy': POLICY, 'scope': self.projects, 'revision': packet['revision'],
                   'nodes': sorted(source_nodes, key=lambda n: n['id']),
                   'edges': sorted(self.edges, key=lambda e: e['id']),
                   'targets': [target_map[t] for t in sorted(used_targets)]}
        self.fingerprint = _digest(binding)
        self.snapshot = {'ok': True, 'scope': self.projects, 'revision': packet['revision'],
                         'nodes': binding['nodes'], 'edges': list(binding['edges']),
                         'targets': binding['targets']}
        prefix = _digest(self.projects)[:16]
        self.slugs = {n['id']: 'sources/alden-' + _digest([self.fingerprint, n['id'], n['source_version'], n['source_target']])
                      for n in source_nodes}
        origin_ids = {}
        for target in sorted(used_targets):
            origin = 'alden-origin:' + _digest([prefix, target])
            if origin in self.nodes:
                raise GbrainError('gbrain_snapshot_duplicate_id')
            origin_ids[target] = origin
            self.nodes[origin] = {'id': origin, 'label': '출처 · ' + target_map[target]['label'],
                'category': 'source', 'source_target': target,
                'basis': 'current authorized registered target; not an entity identity assertion'}
            self.slugs[origin] = 'sources/alden-origin-' + _digest([self.fingerprint, target])
        grouped_memberships = defaultdict(list)
        for identity, target, context in memberships:
            grouped_memberships[identity, target].append(context)
        if len(self.edges) + len(grouped_memberships) > MAX_EDGES:
            raise GbrainError('gbrain_snapshot_edge_budget')
        for (identity, target), contexts in sorted(grouped_memberships.items()):
            self.edges.append({'id': 'alden-origin-edge:' + _digest([self.fingerprint, identity, target]),
                'source': identity, 'target': origin_ids[target], 'relation': 'sourced_from',
                'source_contexts': sorted(contexts, key=_encoded),
                'basis': 'current source membership; not independent corroboration'})
        self.by_slug = {slug: self.nodes[identity] for identity, slug in self.slugs.items()}
        self.relationships = defaultdict(list)
        self.link_types = {}
        for edge in self.edges:
            relation = edge['relation']
            verb = 'sourced_from' if edge['id'].startswith('alden-origin-edge:') else 'alden_' + _digest(relation)[:24]
            if verb in self.link_types and self.link_types[verb] != relation:
                raise GbrainError('gbrain_relation_key_collision')
            self.link_types[verb] = relation
            self.relationships[self.slugs[edge['source']], self.slugs[edge['target']], verb].append(edge)

    def pages(self):
        """Export graph labels/IDs, excluding retained body and description fields."""
        result = []
        for slug, node in sorted(self.by_slug.items()):
            fields = {'type': 'source', 'title': node['label'], 'alden_policy': POLICY,
                      'alden_snapshot': self.fingerprint, 'alden_node_id': node['id'],
                      'alden_source_target': node['source_target']}
            if 'source_version' in node:
                fields['alden_source_version'] = node['source_version']
            content = '---\n' + '\n'.join(k + ': ' + json.dumps(v, ensure_ascii=False)
                for k, v in fields.items()) + '\n---\nAlden scoped graph metadata; not original source text or claim validity.\n'
            result.append({'slug': slug, 'content': content})
        return result

    def manifest(self):
        """Fixed bounded query and metadata only; no ambient/default scope."""
        if len(self.snapshot['nodes']) > 50:
            raise GbrainError('gbrain_manifest_snapshot_budget')
        return _json(_encoded({'version': 1, 'query': {'projects': self.projects, 'limit': 50},
                               'snapshot': self.snapshot, 'fingerprint': self.fingerprint}))

    def links(self):
        return [{'from': a, 'to': b, 'link_type': kind, 'link_source': 'alden-scoped-projection',
                 'context': _encoded({'policy': POLICY, 'snapshot': self.fingerprint,
                                     'edge_ids': sorted(e['id'] for e in edges)}).decode()}
                for (a, b, kind), edges in sorted(self.relationships.items())]

    def schema_pack(self):
        return {'api_version': 'gbrain-schema-pack-v1', 'name': 'alden-scoped-graph',
                'version': '1.0.0', 'gbrain_min_version': VERSION,
                'description': 'Current scoped Alden graph metadata; explicit relation observations, not inferred validity.',
                'extends': 'gbrain-base-v2', 'borrow_from': [],
                'page_types': [], 'takes_kinds': ['fact', 'take', 'bet', 'hunch'],
                'frontmatter_links': [], 'enrichable_types': [], 'filing_rules': [],
                'link_types': [{'name': verb}
                               for verb in sorted(self.link_types) if verb != 'sourced_from']}

    def schema_yaml(self):
        # The pinned pack loader consumes block YAML, not a JSON flow document.
        lines = []
        for name, value in self.schema_pack().items():
            if isinstance(value, str):
                lines.append(name + ': ' + json.dumps(value, ensure_ascii=False))
            elif not value:
                lines.append(name + ': []')
            else:
                lines.append(name + ':')
                for item in value:
                    if isinstance(item, str):
                        lines.append('  - ' + json.dumps(item))
                    else:
                        for index, (key, field) in enumerate(item.items()):
                            lines.append(('  - ' if index == 0 else '    ') + key + ': ' + json.dumps(field, ensure_ascii=False))
        return '\n'.join(lines) + '\n'

    def bind(self, rows, *, focus, current_packet, projects, depth=2, direction='both', link_type=None):
        current = GbrainProjection(current_packet, projects)
        if current.fingerprint != self.fingerprint:
            raise GbrainError('gbrain_projection_stale')
        if (not isinstance(focus, str) or focus not in self.nodes or not isinstance(rows, list)
                or len(rows) > MAX_EDGES or type(depth) is not int or not 1 <= depth <= 3
                or direction not in ('in', 'out', 'both')
                or link_type is not None and (not isinstance(link_type, str) or link_type not in self.link_types)):
            raise GbrainError('gbrain_projection_result_invalid')
        edges, seen, wanted, row_depths = [], set(), {focus}, {}
        for row in rows:
            if not isinstance(row, dict) or row.get('from_source_id') != 'default' or row.get('to_source_id') != 'default':
                raise GbrainError('gbrain_projection_result_unbound')
            key = tuple(_text(row.get(k)) for k in ('from_slug', 'to_slug', 'link_type'))
            expected = self.relationships.get(key)
            if not expected or key in seen or type(row.get('depth')) is not int or not 1 <= row['depth'] <= depth:
                raise GbrainError('gbrain_projection_result_unbound')
            context = _json(row.get('context', ''))
            if context != {'policy': POLICY, 'snapshot': self.fingerprint, 'edge_ids': sorted(e['id'] for e in expected)}:
                raise GbrainError('gbrain_projection_result_unbound')
            seen.add(key)
            row_depths[key] = row['depth']
            for edge in expected:
                edges.append({**edge, 'gbrain_depth': row['depth']})
                wanted.update((edge['source'], edge['target']))
        adjacency = defaultdict(set)
        for edge in edges:
            if direction in ('out', 'both'):
                adjacency[edge['source']].add(edge['target'])
            if direction in ('in', 'both'):
                adjacency[edge['target']].add(edge['source'])
        reached, frontier = {focus}, {focus}
        for _ in range(depth):
            frontier = {n for current_node in frontier for n in adjacency[current_node]} - reached
            reached.update(frontier)
        if not wanted <= reached:
            raise GbrainError('gbrain_projection_path_unbound')
        # Pinned GBrain BFS returns every matching edge incident to a visited
        # endpoint at distance < depth, once, at distance + 1. Compare against
        # the current authoritative projection, not just returned row counts.
        # This detects a partial import or silent truncation even for [] results.
        distance, frontier = {self.slugs[focus]: 0}, {self.slugs[focus]}
        eligible = {key for key in self.relationships if link_type is None or key[2] == link_type}
        neighbours = defaultdict(set)
        for a, b, _ in eligible:
            if direction in ('out', 'both'):
                neighbours[a].add(b)
            if direction in ('in', 'both'):
                neighbours[b].add(a)
        for layer in range(1, depth):
            frontier = {n for node in frontier for n in neighbours[node]} - distance.keys()
            distance.update({node: layer for node in frontier})
        expected_depths = {}
        for key in eligible:
            a, b, _ = key
            walked = ([a] if direction == 'out' else [b] if direction == 'in' else [a, b])
            layers = [distance[n] for n in walked if n in distance]
            if layers:
                expected_depths[key] = min(layers) + 1
        if row_depths != expected_depths:
            raise GbrainError('gbrain_projection_incomplete')
        return {'ok': True, 'nodes': [current.nodes[i] for i in sorted(wanted)], 'edges': edges,
                'scope': self.projects, 'basis': {'engine': 'gbrain', 'policy': POLICY,
                'snapshot': self.fingerprint, 'source_authority': 'current Alden scoped graph',
                'checked_edges': len(expected_depths), 'depth': depth, 'direction': direction,
                'link_type': link_type, 'complete_within_snapshot': True,
                'scope_limit': 'same bounded source snapshot; not complete corpus or semantic truth'}}
