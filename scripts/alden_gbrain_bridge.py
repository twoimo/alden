"""Opt-in read bridge; only current, explicitly scoped Alden metadata is returned.

Provisioning is a separate operation. This module never imports pages, creates a
store, registers a host, or follows instructions in upstream response notices.
"""
from alden_gbrain_client import GbrainError, GbrainReadClient, _json, _path
from alden_gbrain_projection import GbrainProjection


class GbrainGraphReader:
    def __init__(self, *, owned_root, receipt, home, manifest):
        self.root, self.receipt, self.home, self.manifest = map(_path, (owned_root, receipt, home, manifest))
        if any(not p.is_relative_to(self.root) for p in (self.receipt, self.home, self.manifest)):
            raise GbrainError('gbrain_root_mismatch')

    def query(self, *, projects, focus, current, check=lambda: None, depth=2, direction='both'):
        check()
        path = _path(self.manifest)
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise GbrainError('gbrain_manifest_unavailable')
        manifest = _json(path.read_bytes())
        if (not isinstance(manifest, dict) or set(manifest) != {'version', 'query', 'snapshot', 'fingerprint'}
                or type(manifest['version']) is not int or manifest['version'] != 1
                or not isinstance(projects, list) or any(not isinstance(p, str) for p in projects)
                or manifest['query'] != {'projects': sorted(projects), 'limit': 50}):
            raise GbrainError('gbrain_manifest_scope_mismatch')
        projection = GbrainProjection(manifest['snapshot'], projects)
        if manifest['fingerprint'] != projection.fingerprint:
            raise GbrainError('gbrain_manifest_changed')
        if type(depth) is not int or not 1 <= depth <= 3 or direction not in ('in', 'out', 'both'):
            raise GbrainError('gbrain_graph_options_invalid')
        # Check permission/version before opening the cache as well as after it.
        if GbrainProjection(current(), projects).fingerprint != projection.fingerprint:
            raise GbrainError('gbrain_projection_stale')
        if not isinstance(focus, str) or focus not in projection.nodes:
            raise GbrainError('gbrain_focus_unavailable')
        with GbrainReadClient(self.receipt, owned_root=self.root, home=self.home, check=check) as client:
            rows = client.graph(projection.slugs[focus], depth=depth, direction=direction)
            check()
            result = projection.bind(rows, focus=focus, current_packet=current(), projects=projects,
                                     depth=depth, direction=direction)
        check()
        return result
