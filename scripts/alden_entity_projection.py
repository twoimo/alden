"""Display confirmed, current duplicate groups without rewriting source facts."""
from copy import deepcopy


def project_groups(graph, groups, project):
    """Only a complete current group in one exact project can replace its nodes.

    A partial/filter page keeps originals. Relation IDs, direction, evidence and
    original endpoints remain recoverable, including relations inside a group.
    Full source counts stay source counts; displayed counts are reported apart.
    """
    if not isinstance(graph, dict) or graph.get("ok") is not True:
        return graph
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return graph
    indexed = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    if len(indexed) != len(nodes):
        return graph
    choices, accepted, claimed = {}, {}, set()
    for group in groups:
        if not isinstance(group, dict) or group.get("project") != project or group.get("state") != "active":
            continue
        members, canonical = group.get("members"), group.get("canonical")
        if not isinstance(members, list) or not 2 <= len(members) <= 16 or not isinstance(canonical, dict):
            continue
        keys = [m.get("document_id") for m in members if isinstance(m, dict)]
        if len(keys) != len(members) or len(set(keys)) != len(keys) or any(k not in indexed for k in keys):
            continue
        if claimed.intersection(keys):
            continue
        if any(indexed[m["document_id"]].get("source_target") != m.get("target_id")
               or indexed[m["document_id"]].get("source_version") != m.get("version") for m in members):
            continue
        representative = canonical.get("document_id")
        if representative not in keys or indexed[representative].get("source_target") != canonical.get("target_id"):
            continue
        identity = group.get("resolution_id")
        if not isinstance(identity, str) or not identity:
            continue
        claimed.update(keys)
        choices.update((key, representative) for key in keys)
        accepted[representative] = group
    if not accepted:
        return graph
    result = deepcopy(graph)
    originals = deepcopy(nodes)
    hidden = {key for key, canonical in choices.items() if key != canonical}
    result["nodes"] = [n for n in result["nodes"] if n["id"] not in hidden]
    for node in result["nodes"]:
        group = accepted.get(node["id"])
        if not group:
            continue
        node["resolution"] = {
            "resolution_id": group["resolution_id"], "project": project,
            "reason": group.get("reason", ""), "kind": "explicit_user_decision",
            "members": [deepcopy(indexed[m["document_id"]]) for m in group["members"]],
        }
        node["resolution_contexts"] = [{**deepcopy(node["resolution"]), "consolidated": True, "display_matches": True}]
    for edge in result["edges"]:
        source, target = edge.get("source"), edge.get("target")
        if source in choices or target in choices:
            edge["original_source"], edge["original_target"] = source, target
            edge["source"], edge["target"] = choices.get(source, source), choices.get(target, target)
            edge["display_resolution"] = "explicit_user_decision"
    result["resolution_projection"] = {
        "project": project, "groups": len(accepted), "hidden_sources": len(hidden),
        "source_nodes": len(originals), "display_nodes": len(result["nodes"]),
        "source_edges": len(edges), "display_edges": len(result["edges"]),
        "counts_scope": "this bounded page only; total_nodes/total_edges retain source scope",
        "original_nodes": originals,
        "internal_relations": [e for e in result["edges"] if e.get("source") == e.get("target")],
    }
    return result


def project_context_groups(graph, groups, projects):
    """Project scoped decisions into a union without erasing another context.

    Each permitted membership/version must have an explicit decision for the
    same complete member set and representative before a union can collapse it.
    Otherwise the original nodes retain their project-specific review notes.
    This is a display decision, never new source identity or transitive truth.
    """
    if not isinstance(graph, dict) or graph.get("ok") is not True:
        return graph
    if not isinstance(projects, list) or len(projects) < 2 or any(not isinstance(p, str) for p in projects):
        return graph
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return graph
    if any(not isinstance(n, dict) or not isinstance(n.get("id"), str) for n in nodes):
        return graph
    indexed = {n["id"]: n for n in nodes}
    if len(indexed) != len(nodes):
        return graph
    scope = set(projects)
    contexts = {}
    for node in nodes:
        permitted = node.get("source_contexts")
        if not isinstance(permitted, list) or not permitted:
            continue
        resolved = {}
        for item in permitted:
            if not isinstance(item, dict) or not isinstance(item.get("project"), str) or item["project"] not in scope:
                break
            values = tuple(item.get(k) for k in ("project", "target_id", "version"))
            if any(not isinstance(v, str) or not v for v in values):
                break
            resolved[values] = item
        else:
            if any(key[1:] == (node.get("source_target"), node.get("source_version")) for key in resolved):
                contexts[node["id"]] = resolved
    reviews = []
    for group in groups:
        if not isinstance(group, dict) or group.get("state") != "active" or not isinstance(group.get("project"), str) or group["project"] not in scope:
            continue
        members, canonical, identity = group.get("members"), group.get("canonical"), group.get("resolution_id")
        if not isinstance(members, list) or not 2 <= len(members) <= 16 or not isinstance(canonical, dict) or not isinstance(identity, str) or not identity:
            continue
        if any(not isinstance(m, dict) or any(not isinstance(m.get(k), str) or not m[k] for k in ("document_id", "target_id", "version")) for m in members):
            continue
        identifiers = [m["document_id"] for m in members]
        if len(set(identifiers)) != len(members) or any(i not in contexts for i in identifiers):
            continue
        representative = canonical.get("document_id")
        if representative not in identifiers or not any(m["document_id"] == representative and m["target_id"] == canonical.get("target_id") for m in members):
            continue
        member_contexts = [(m["document_id"], (group["project"], m["target_id"], m["version"])) for m in members]
        if any(key not in contexts[identifier] for identifier, key in member_contexts):
            continue
        signature = (tuple(sorted(identifiers)), representative)
        metadata = {
            "resolution_id": identity, "project": group["project"], "reason": group.get("reason", ""),
            "kind": "explicit_user_decision", "consolidated": False,
            "display_matches": all(indexed[m["document_id"]].get("source_target") == m["target_id"]
                                   and indexed[m["document_id"]].get("source_version") == m["version"] for m in members),
            "members": [{"id": identifier, "label": contexts[identifier][key].get("label", ""),
                         "source_target": key[1], "source_version": key[2]} for identifier, key in member_contexts],
        }
        reviews.append((signature, member_contexts, metadata))
    if not reviews:
        return graph
    # Two conflicting reviews of one context never authorize a global choice.
    votes = {}
    for signature, member_contexts, _ in reviews:
        for identifier, key in member_contexts:
            votes.setdefault((identifier, key), set()).add(signature)
    accepted = {}
    for signature, _, _ in reviews:
        if signature in accepted:
            continue
        identifiers, representative = signature
        if all(votes.get((identifier, key)) == {signature} for identifier in identifiers for key in contexts[identifier]):
            accepted[signature] = representative
    choices = {identifier: representative for (identifiers, _), representative in accepted.items() for identifier in identifiers}
    result = deepcopy(graph)
    hidden = {identifier for identifier, representative in choices.items() if identifier != representative}
    result["nodes"] = [node for node in result["nodes"] if node["id"] not in hidden]
    notes = {}
    for signature, member_contexts, metadata in reviews:
        metadata["consolidated"] = signature in accepted
        for identifier, _ in member_contexts:
            visible = choices.get(identifier, identifier)
            notes.setdefault(visible, {})[(metadata["project"], metadata["resolution_id"])] = metadata
    for node in result["nodes"]:
        if node["id"] in notes:
            node["resolution_contexts"] = [deepcopy(value) for _, value in sorted(notes[node["id"]].items())]
    for edge in result["edges"]:
        source, target = edge.get("source"), edge.get("target")
        if source in choices or target in choices:
            edge["original_source"], edge["original_target"] = source, target
            edge["source"], edge["target"] = choices.get(source, source), choices.get(target, target)
            edge["display_resolution"] = "explicit_user_decision"
    result["resolution_projection"] = {
        "projects": sorted(scope), "groups": len(accepted), "decision_contexts": len(reviews),
        "hidden_sources": len(hidden), "source_nodes": len(nodes), "display_nodes": len(result["nodes"]),
        "source_edges": len(edges), "display_edges": len(result["edges"]),
        "counts_scope": "this bounded page only; total_nodes/total_edges retain source scope",
        "original_nodes": deepcopy(nodes),
        "internal_relations": [edge for edge in result["edges"] if edge.get("source") == edge.get("target")],
    }
    return result
