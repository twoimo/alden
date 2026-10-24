"""Independent focus-view BFS oracle over small, private CollectionStore fixtures.

Expected vertices and edges come from the fixture's original records and
relationships, never from graph_page, its SQL, or another graph projection.
"""

from collections import defaultdict
from itertools import combinations
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, encoded, identity


def record(name, *, platform="graph", kind="topic", label=None):
    return {"platform": platform, "original_id": name, "kind": kind,
            "label": label or name, "text": "Original evidence for " + name,
            "source_url": "local://focus-fixture/" + name}


def relationship(source, target, kind="links", *, platform="graph", key=""):
    return {"source_platform": platform, "source_id": source,
            "target_platform": platform, "target_id": target, "type": kind,
            "original_id": key, "evidence": {"fixture": "explicit", "key": key}}


class ReferenceFocusCase(unittest.TestCase):
    """Stores source manifests separately from the graph_page read boundary."""

    def setUp(self):
        temporary = TemporaryDirectory(prefix="alden-focus-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.store = CollectionStore(Path(temporary.name).resolve())
        self.sources = []

    def add_source(self, name, records, relations, *, projects, permission="local-private",
                   platform="graph", collected_at=100):
        target = self.store.register(platform=platform, original_id="focus-" + name,
                                     kind="source", label=name, projects=projects,
                                     permission=permission)
        self.store.ingest(target, records, relations=relations)
        entries = {}
        # Set clock values only in this temporary fixture to test exact time
        # boundaries without sleeping or depending on wall-clock ordering.
        with self.store.database() as db:
            for item in records:
                document = identity(item["platform"], item["original_id"])
                version = db.execute(
                    "SELECT current_version FROM memberships WHERE target_id=? AND document_id=?",
                    (target, document)).fetchone()[0]
                at = (collected_at[item["original_id"]]
                      if isinstance(collected_at, dict) else collected_at)
                db.execute("UPDATE versions SET collected_at=? WHERE id=?", (at, version))
                entries[document] = {
                    "version": version, "at": at, "kind": item["kind"],
                    "label": item["label"], "url": item["source_url"],
                    "platform": item["platform"],
                }
        self.sources.append({"target": target, "projects": set(projects),
                             "permission": permission, "platform": platform,
                             "entries": entries, "relations": relations})
        return target

    def reference(self, *, projects, focus, hops, limit, target_id=None, platform=None,
                  node_type=None, relation=None, since=None, until=None):
        """Layer-by-layer BFS based solely on admitted original relationships."""
        requested = set(projects)
        admitted = [source for source in self.sources
                    if source["permission"] != "denied"
                    and requested.intersection(source["projects"])
                    and (target_id is None or source["target"] == target_id)
                    and (platform is None or source["platform"] == platform)]
        memberships = defaultdict(list)
        for source in admitted:
            for document, entry in source["entries"].items():
                memberships[document].append((source, entry))

        def passes(entry):
            return ((node_type is None or entry["kind"] == node_type)
                    and (since is None or entry["at"] >= since)
                    and (until is None or entry["at"] < until))

        displayed = {}
        for document, choices in memberships.items():
            # Select the newest *permitted* source first, then apply document
            # type and time filtering; a hidden/older revision cannot win.
            source, entry = min(choices, key=lambda pair: (-pair[1]["at"],
                                                            pair[0]["target"]))
            if passes(entry):
                displayed[document] = (source, entry)

        permitted_edges = []
        for source in admitted:
            for original in source["relations"]:
                begin = identity(original["source_platform"], original["source_id"])
                end = identity(original["target_platform"], original["target_id"])
                if begin not in displayed or end not in displayed:
                    continue
                if relation is not None and original["type"] != relation:
                    continue
                relation_id = identity("relation", encoded([
                    source["target"], begin, original["type"], end,
                    original.get("original_id", "")]).decode())
                permitted_edges.append((relation_id, begin, end, original["type"]))

        shown = (set(displayed) if relation is None else
                 {vertex for _, begin, end, _ in permitted_edges for vertex in (begin, end)})
        if focus not in shown:
            return None

        neighbors = defaultdict(set)
        for _, begin, end, _ in permitted_edges:
            if begin != end:
                neighbors[begin].add(end)
                neighbors[end].add(begin)

        # A true frontier-based BFS: order each complete distance layer by
        # immutable document ID, then enforce the original 24-node budget.
        budget = min(limit, 24)
        discovered = [focus]
        visited = {focus}
        frontier = {focus}
        for _ in range(hops):
            candidates = set()
            for vertex in frontier:
                candidates.update(neighbors[vertex])
            next_layer = sorted(candidates - visited)[:budget - len(discovered)]
            if not next_layer:
                break
            discovered.extend(next_layer)
            visited.update(next_layer)
            frontier = set(next_layer)
            if len(discovered) == budget:
                break

        chosen = set(discovered)
        edges = sorted((item for item in permitted_edges
                        if item[1] in chosen and item[2] in chosen))[:144]
        expected_nodes = {}
        for document in chosen:
            source, entry = displayed[document]
            contexts = []
            for context_source, context_entry in memberships[document]:
                if not passes(context_entry):
                    continue
                for project in context_source["projects"].intersection(requested):
                    contexts.append({"project": project,
                                     "target_id": context_source["target"],
                                     "version": context_entry["version"],
                                     "label": context_entry["label"]})
            contexts.sort(key=lambda item: (item["project"], item["target_id"],
                                            item["version"]))
            expected_nodes[document] = {
                "degree": len(neighbors[document]), "source_contexts": contexts,
                "source_version": entry["version"], "source_target": source["target"],
                "source_platform": entry["platform"], "label": entry["label"],
                "source_url": entry["url"], "updated_at": entry["at"],
                "space": ", ".join(sorted(source["projects"].intersection(requested))),
            }
        return {"nodes": expected_nodes, "edges": edges,
                "total_nodes": len(shown), "total_edges": len(permitted_edges)}

    def assert_focus_matches_reference(self, *, projects, focus, hops=1, limit=24,
                                       **filters):
        oracle = self.reference(projects=projects, focus=focus, hops=hops,
                                limit=limit, **filters)
        arguments = dict(projects=projects, focus=focus, hops=hops, limit=limit,
                         **filters)
        if oracle is None:
            with self.assertRaisesRegex(ValueError, "collection_graph_focus_not_in_scope"):
                self.store.graph_page(**arguments)
            return None
        result = self.store.graph_page(**arguments)
        self.assertTrue(result["ok"])
        self.assertEqual([node["id"] for node in result["nodes"]],
                         sorted(oracle["nodes"]))
        self.assertEqual([(edge["id"], edge["source"], edge["target"], edge["relation"])
                          for edge in result["edges"]], oracle["edges"])
        self.assertEqual((result["total_nodes"], result["total_edges"]),
                         (oracle["total_nodes"], oracle["total_edges"]))
        self.assertLessEqual(len(result["nodes"]), min(24, limit))
        self.assertLessEqual(len(result["edges"]), 144)
        self.assertIsNone(result["next"])
        for node in result["nodes"]:
            with self.subTest(document=node["id"]):
                for field, expected in oracle["nodes"][node["id"]].items():
                    self.assertEqual(node[field], expected, field)
                self.assertEqual(node["degree_scope"], "permitted filtered graph")
        return result


class DenseFocusBFSRegressionTests(ReferenceFocusCase):
    def setUp(self):
        super().setUp()
        names = ["n%02d" % index for index in range(28)]
        original_edges = [relationship("n00", name) for name in names[1:25]]
        original_edges.extend(relationship(a, b, "dense")
                              for a, b in combinations(names[1:25], 2))
        original_edges.extend([
            relationship("n00", "n01", key="parallel-same-type"),
            relationship("n00", "n01", "supports"),
            relationship("n01", "n00"),
            relationship("n00", "n00", "loop"),
            relationship("n01", "n25", "chain"),
            relationship("n25", "n26", "chain"),
            relationship("n26", "n27", "chain"),
        ])
        self.add_source("dense", [record(name) for name in names], original_edges,
                        projects=["focus"])
        self.hub = identity("graph", "n00")
        self.tail = identity("graph", "n27")

    def test_all_hop_and_limit_combinations_use_reference_distance_and_id_order(self):
        for focus in (self.hub, self.tail):
            for hops in range(4):
                for limit in range(1, 25):
                    with self.subTest(focus=focus, hops=hops, limit=limit):
                        self.assert_focus_matches_reference(
                            projects=["focus"], focus=focus, hops=hops, limit=limit)

    def test_dense_cap_preserves_global_degree_and_original_edge_id_prefix(self):
        result = self.assert_focus_matches_reference(
            projects=["focus"], focus=self.hub, hops=1, limit=24)
        self.assertEqual(len(result["nodes"]), 24)
        self.assertEqual(len(result["edges"]), 144)
        self.assertGreater(result["total_edges"], 144)
        self.assertEqual(next(node["degree"] for node in result["nodes"]
                              if node["id"] == self.hub), 24)
        # More generous API limit must not lift the focus-specific cap.
        for limit in (25, 120):
            with self.subTest(limit=limit):
                self.assert_focus_matches_reference(
                    projects=["focus"], focus=self.hub, hops=3, limit=limit)

    def test_relation_filtered_direction_parallel_edges_and_self_loop(self):
        for relation in ("links", "dense", "supports", "loop", "chain"):
            for focus in (self.hub, self.tail):
                with self.subTest(relation=relation, focus=focus):
                    self.assert_focus_matches_reference(
                        projects=["focus"], focus=focus, hops=3,
                        limit=24, relation=relation)
        self.assert_focus_matches_reference(
            projects=["focus"], focus=self.hub, hops=0, limit=1)


class ScopedFocusBFSRegressionTests(ReferenceFocusCase):
    def test_fractional_source_clock_is_preserved_exactly(self):
        at = 1791461432.6890361
        self.add_source('clock', [record('clock')], [], projects=['clock'], collected_at=at)
        self.assert_focus_matches_reference(projects=['clock'], focus=identity('graph', 'clock'),
                                            hops=0, limit=1)

    def setUp(self):
        super().setUp()
        self.red = self.add_source("red", [
            record("root", label="Red original"), record("a"),
            record("b", kind="note"), record("shift"), record("solo"),
            record("old")], [
                relationship("root", "a"), relationship("a", "b", "references"),
                relationship("b", "root", "opposes"),
                relationship("root", "root", "loop"),
                relationship("root", "a", "supports"),
                relationship("a", "root"), relationship("root", "shift"),
                relationship("old", "root", "historical")],
            projects=["red", "joint"], collected_at={
                "root": 100, "a": 100, "b": 100, "shift": 100,
                "solo": 100, "old": 80})
        self.blue = self.add_source("blue", [
            record("root", label="Blue original"),
            record("shift", kind="note", label="Blue shift"), record("c")], [
                relationship("root", "c"), relationship("shift", "c", "supports"),
                relationship("c", "a", "cross-source")],
            projects=["blue", "joint"], collected_at=300)
        self.denied = self.add_source("denied", [
            record("root", label="Denied revision"), record("secret")],
            [relationship("secret", "root", "forbidden")],
            projects=["red", "joint"], permission="denied", collected_at=500)
        self.outside = self.add_source("outside", [
            record("root", label="Outside revision"), record("outsider")],
            [relationship("outsider", "root")],
            projects=["else"], collected_at=700)
        self.youtube = self.add_source("youtube", [
            record("video", platform="youtube"),
            record("clip", platform="youtube")], [
                relationship("video", "clip", platform="youtube")],
            projects=["red"], platform="youtube", collected_at=200)

    def test_project_target_permission_platform_relation_type_and_time_scopes(self):
        cases = [
            (["red"], "root", {}),
            (["blue"], "root", {}),
            (["joint"], "root", {}),
            (["red", "blue"], "root", {}),
            (["red", "blue", "joint"], "root", {}),
            (["else"], "root", {}),
            (["red"], "root", {"target_id": self.red}),
            (["blue"], "root", {"target_id": self.blue}),
            (["red", "blue"], "root", {"target_id": self.blue}),
            (["red"], "root", {"target_id": self.blue}),
            (["red"], "root", {"target_id": self.denied}),
            (["red"], "root", {"target_id": self.outside}),
            (["red"], "root", {"platform": "graph"}),
            (["red"], "root", {"platform": "youtube"}),
            (["red"], "video", {"platform": "youtube"}),
            (["red"], "root", {"node_type": "topic"}),
            (["red"], "b", {"node_type": "note"}),
            (["red", "blue"], "shift", {"node_type": "note"}),
            (["red", "blue"], "shift", {"node_type": "topic"}),
            (["red"], "root", {"relation": "links"}),
            (["red", "blue"], "root", {"relation": "links"}),
            (["red"], "root", {"relation": "supports"}),
            (["red"], "root", {"relation": "loop"}),
            (["red"], "root", {"relation": "forbidden"}),
            (["blue"], "a", {}),
            (["red"], "secret", {}),
            (["red"], "outsider", {}),
            (["red"], "root", {"since": 100, "until": 300}),
            (["red", "blue"], "root", {"since": 100, "until": 300}),
            (["red", "blue"], "root", {"since": 300}),
            (["red", "blue"], "root", {"until": 300}),
            (["red"], "root", {"since": 101}),
            (["red"], "root", {"since": 100, "until": 101}),
            (["red", "blue"], "shift", {"since": 300, "node_type": "note"}),
            (["red", "blue"], "shift", {"until": 300, "node_type": "topic"}),
        ]
        for projects, name, filters in cases:
            focus = identity("youtube" if name == "video" else "graph", name)
            with self.subTest(projects=projects, focus=name, filters=filters):
                self.assert_focus_matches_reference(
                    projects=projects, focus=focus, hops=3, limit=24, **filters)

    def test_original_multi_project_source_contexts_survive_limited_focus(self):
        root = identity("graph", "root")
        for projects, filters in [
            (["red"], {}), (["blue"], {}), (["joint"], {}),
            (["red", "blue", "joint"], {}),
            (["red", "blue", "joint"], {"since": 200}),
            (["red", "blue", "joint"], {"target_id": self.red}),
        ]:
            for hops in (0, 1, 3):
                with self.subTest(projects=projects, filters=filters, hops=hops):
                    self.assert_focus_matches_reference(
                        projects=projects, focus=root, hops=hops, limit=2,
                        **filters)


if __name__ == "__main__":
    unittest.main()
