"""Bounded public-code probe; run in an isolated Graphify audit environment.

No private corpus, network, LLM, product writes or Graphify installation hooks.
This is a counterexample suite, not an independently sampled quality benchmark.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    upstream = args.upstream.resolve(strict=True)
    expected = "86c57be8204673c2a1b9849a3292a7dd3ab373d8"
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != expected:
        raise SystemExit("Unreviewed upstream revision")
    for name in ["graphify/cluster.py", "graphify/dedup.py", "graphify/build.py"]:
        subprocess.run(["git", "-C", str(upstream), "diff", "--exit-code", "HEAD", "--", name], check=True)
    sys.path.insert(0, str(upstream))
    import networkx as nx
    from graphify.cluster import _partition, cluster, label_communities_by_hub
    from graphify.dedup import deduplicate_entities

    native = importlib.util.find_spec("graspologic_native") is not None
    wrapper = importlib.util.find_spec("graspologic") is not None
    if native or wrapper:
        raise SystemExit("This receipt is scoped to the default Louvain fallback; Leiden present")

    cases = [
        ("same_name_distinct_people", "김민수", "김민수", "person-A", "person-B", "L1", "L9", False),
        ("different_programming_languages", "C++", "C#", "cpp", "csharp", "L1", "L9", False),
        ("quantity_sign", "10 mg", "-10 mg", "positive-dose", "negative-dose", "L1", "L9", False),
        ("decimal_vs_date", "3.14", "3/14", "decimal", "calendar-date", "L1", "L9", False),
        ("true_repeat", "Knowledge Graph", "knowledge   graph", "same-entity", "same-entity", "L1", "L1", True),
        ("numeric_siblings", "GPT-6", "GPT-7", "version6", "version7", "L1", "L9", False),
    ]
    results = []
    for key, left, right, identity_l, identity_r, loc_l, loc_r, expected_merge in cases:
        nodes = [
            {"id": "n1", "label": left, "file_type": "concept", "source_file": "fictional.md", "source_location": loc_l, "entity_identity": identity_l},
            {"id": "n2", "label": right, "file_type": "concept", "source_file": "fictional.md", "source_location": loc_r, "entity_identity": identity_r},
        ]
        merged, edges = deduplicate_entities(copy.deepcopy(nodes), [], communities={"n1": 0, "n2": 0})
        did_merge = len(merged) == 1
        results.append({"case": key, "input": nodes, "output": merged, "merged": did_merge,
                        "desired_for_alden": expected_merge, "matches_alden_identity_contract": did_merge == expected_merge})

    controls = []
    for name, overrides, raises in [
        ("missing_provenance", [{"source_file": ""}, {"source_file": ""}], False),
        ("cross_repository", [{"repo": "one"}, {"repo": "two"}], True),
        ("protected_incremental", [{}, {}], False),
    ]:
        nodes = [{"id": f"n{i}", "label": "Knowledge Graph", "file_type": "concept", "source_file": f"file{i}.md", **extra} for i, extra in enumerate(overrides)]
        try:
            ns, _ = deduplicate_entities(nodes, [], communities={}, **({"protected_ids": {"n0", "n1"}} if name == "protected_incremental" else {}))
            controls.append({"case": name, "nodes": len(ns), "raised": False, "pass": not raises and len(ns) == 2})
        except ValueError:
            controls.append({"case": name, "raised": True, "pass": raises})

    graph = nx.Graph()
    graph.add_node("hub", label="일반 동영상")
    for i in range(8):
        graph.add_node(f"useful{i}", label=f"구체적인 면접 조언 {i}")
        graph.add_edge("hub", f"useful{i}")
    hub_label = label_communities_by_hub(graph, {0: list(graph)})

    a = nx.Graph()
    a.add_edges_from([(str(u), str(v)) for u, v in [(0,1),(1,2),(2,0),(3,4),(4,5),(5,3),(2,3)]])
    extended = nx.compose(a, nx.relabel_nodes(nx.complete_graph(20), lambda n: "unrelated:" + str(n)))
    def groups(mapping, restrict=None):
        out = {}
        for node, group in mapping.items():
            if restrict is None or node in restrict:
                out.setdefault(group, []).append(node)
        return sorted(sorted(v) for v in out.values())
    objective = {"partition_A": groups(_partition(a)),
                 "partition_A_with_unrelated_K20": groups(_partition(extended), set(a)),
                 "cluster_A": groups({n:c for c, ns in cluster(a).items() for n in ns}),
                 "cluster_A_with_unrelated_K20": groups({n:c for c, ns in cluster(extended).items() for n in ns}, set(a))}
    result = {"schema": 1, "upstream_revision": revision,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "backend": "networkx Louvain fallback; no Leiden bindings installed",
              "packages": {p: importlib.metadata.version(p) for p in ["networkx", "numpy", "rapidfuzz"]},
              "source_sha256": {p: hashlib.sha256((upstream / p).read_bytes()).hexdigest() for p in ["graphify/cluster.py", "graphify/dedup.py", "graphify/build.py"]},
              "dedup_cases": results, "controls": controls,
              "generic_hub_label": hub_label, "objective": objective,
              "limits": "Six hand-authored counterexamples and three guard controls. No corpus quality, resource speedup, Leiden behavior, or product integration claim."}
    if not all(row["pass"] for row in controls):
        raise SystemExit("Upstream guard control failed")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"cases": len(results), "contract_mismatches": sum(not r["matches_alden_identity_contract"] for r in results), "controls_passed": len(controls), "generic_label": hub_label, "objective": objective}, ensure_ascii=False))


if __name__ == "__main__":
    main()
