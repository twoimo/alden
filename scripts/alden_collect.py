#!/usr/bin/env python3
"""Deterministic adapters for declared local/public source snapshots.

Live site acquisition belongs to the supported host browser/API. This command
consumes its provenance-preserving snapshot without borrowing cookies or sending.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import parse_qs, urlparse

from alden_collection import CollectionStore, encoded, identity
from alden_abort import ABORT_STATE_NAME, AbortToken

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
BATCH_SIZE = 1000


def json_snapshot(path: Path, *, include_raw=False):
    path = path.absolute()
    if any(p.is_symlink() for p in [*path.parents, path]):
        raise ValueError("collection_source_symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("collection_snapshot_budget")
        raw = source.read(MAX_SNAPSHOT_BYTES + 1)
        after = os.fstat(source.fileno())
    current = path.stat(follow_symlinks=False)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current):
        raise RuntimeError("collection_source_changed_during_read")
    result = (json.loads(raw), {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                               "mtime_ns": before.st_mtime_ns, "observed_at": time.time()})
    return (*result, raw) if include_raw else result


def source_graph(target: dict, data: dict, source: dict):
    """Preserve native IDs and explicit edge direction/type; infer nothing."""
    prefix = target["original_id"]
    if not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not isinstance(data.get('edges'), list):
        raise ValueError('collection_incomplete_source_shape')
    records, relations, mappings = [], [], {}
    for node in data.get("nodes", []):
        original = prefix + ":node:" + str(node["id"])
        mappings[str(node["id"])] = ("graph", original)
        records.append({"platform": "graph", "original_id": original, "label": node.get("label", node.get("title", str(node["id"]))),
                        "kind": node.get("type", node.get("kind", "source_node")),
                        "text": node.get("localOriginalText") or node.get("summary", ""),
                        "text_field": "localOriginalText" if node.get("localOriginalText") else "summary",
                        "source": source, "source_node_id": str(node["id"]), "raw": node,
                        "truth_status": "source_record; not independently verified"})
    for edge in data.get("edges", []):
        a, b = str(edge["source"]), str(edge["target"])
        if a not in mappings or b not in mappings:
            raise ValueError("collection_source_orphan_edge")
        relations.append({"source_platform": "graph", "source_id": mappings[a][1],
                          "target_platform": "graph", "target_id": mappings[b][1],
                          "type": edge.get("kind", edge.get("type", "reference")),
                          "original_id": str(edge.get("id", "")), "evidence": {"source": source, "raw": edge}})
    return records, relations


def youtube_graph(target: dict, data: dict, source: dict):
    channel = data.get("channel", {})
    if not all(isinstance(data.get(key), list) for key in ['series', 'videos', 'claims', 'relations']):
        raise ValueError('collection_incomplete_source_shape')
    if channel.get("id") != target["original_id"]:
        raise ValueError("collection_channel_identity_mismatch")
    records, mappings, relations = [], {}, []
    for series in data.get("series", []):
        original = channel["id"] + ":series:" + str(series["id"])
        mappings["series:" + str(series["id"])] = ("graph", original)
        records.append({"platform": "graph", "original_id": original, "label": series["label"],
                        "kind": "source_group", "text": "", "source": source, "raw": series})
    for video in data.get("videos", []):
        video_id = str(video["id"]).removeprefix("video:")
        mappings["video:" + video_id] = ("youtube", video_id)
        records.append({"platform": "youtube", "original_id": video_id, "label": video["title"],
                        "kind": "video", "text": video.get("description", ""), "author_id": channel["id"],
                        "url": video["url"], "published_at": video.get("date"), "source": source,
                        "raw": video, "coverage": "metadata and supplied evidence; not full-video analysis"})
    for claim in data.get("claims", []):
        claim_id = str(claim["id"])
        original = channel["id"] + ":claim:" + claim_id
        mappings[claim_id] = ("graph", original)
        mappings.setdefault("claim:" + claim_id, ("graph", original))
        records.append({"platform": "graph", "original_id": original, "label": claim.get("title", claim_id),
                        "kind": "source_claim", "text": claim.get("text", ""), "source": source,
                        "url": claim.get("sourceUrl", ""), "evidence_location": claim.get("timestamp"),
                        "truth_status": "source claim; retain stated limits", "raw": claim})
    for edge in data.get("relations", []):
        a, b = edge["source"], edge["target"]
        if a not in mappings or b not in mappings:
            raise ValueError("collection_source_orphan_edge")
        relations.append({"source_platform": mappings[a][0], "source_id": mappings[a][1],
                          "target_platform": mappings[b][0], "target_id": mappings[b][1],
                          "type": edge["type"], "evidence": {"source": source, "raw": edge}})
    return records, relations


def threads_snapshot(target: dict, data: dict, source: dict):
    records, relations = [], []
    if not isinstance(data, dict) or not isinstance(data.get('posts'), list):
        raise ValueError('collection_incomplete_source_shape')
    for post in data.get("posts", []):
        if post.get("author_id") != target["original_id"]:
            raise ValueError("collection_threads_author_mismatch")
        if post.get("availability", "available") != "available":
            raise RuntimeError("collection_threads_access_not_confirmed")
        records.append({"platform": "threads", "original_id": str(post["id"]),
                        "label": post.get("title", post["text"][:80]), "kind": "post", "text": post["text"],
                        "author_id": post["author_id"], "url": post["url"], "published_at": post.get("published_at"),
                        "source": source, "raw": post})
    return records, relations


def youtube_video_snapshot(target: dict, data: dict, source: dict):
    """One observed video, isolated from a channel's complete membership set."""
    if target['platform']!='youtube' or target['kind']!='video':
        raise ValueError('collection_video_target_scope_required')
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('videos'),list) or len(data['videos'])!=1:
        raise ValueError('collection_incomplete_source_shape')
    raw=data['videos'][0]
    if not isinstance(raw,dict) or not isinstance(raw.get('metadata'),dict):
        raise ValueError('collection_incomplete_source_shape')
    metadata=raw['metadata'];video_id=metadata.get('videoId')
    if video_id!=target['original_id'] or not isinstance(video_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
        raise ValueError('collection_video_identity_mismatch')
    url=metadata.get('url');parsed=urlparse(url) if isinstance(url,str) else None
    if parsed is None or parsed.scheme!='https' or parsed.netloc!='www.youtube.com' or parsed.path!='/watch' or parse_qs(parsed.query)!= {'v':[video_id]}:
        raise ValueError('collection_video_url_identity_mismatch')
    if not isinstance(metadata.get('title'),str) or not metadata['title'].strip() or not isinstance(metadata.get('description',''),str):
        raise ValueError('collection_incomplete_source_shape')
    captions=raw.get('captions',{'availability':'not_requested'})
    if not isinstance(captions,dict) or captions.get('availability') not in {'available','access_failed','not_requested'}:
        raise ValueError('collection_caption_state_invalid')
    text=metadata.get('description','');language=captions.get('language');auto=None
    if captions['availability']=='available':
        transcript=captions.get('text')
        if not isinstance(language,str) or not language or not isinstance(transcript,str) or not transcript.strip():
            raise ValueError('collection_caption_content_unconfirmed')
        if not all(re.match(r'^\[\d+:\d{2} - \d+:\d{2}\] .+',line) for line in transcript.splitlines() if line.strip()):
            raise ValueError('collection_caption_timestamp_unconfirmed')
        tracks=raw.get('tracks',[])
        if not isinstance(tracks,list) or not all(isinstance(t,dict) for t in tracks):
            raise ValueError('collection_caption_state_invalid')
        matching=[t.get('isAutoGenerated') for t in tracks if t.get('languageCode')==language]
        if matching and all(type(value) is bool for value in matching) and len(set(matching))==1:auto=matching[0]
        text+='\n\n[자막 '+language+' · 종류 '+('자동' if auto is True else '수동' if auto is False else '미확인')+']\n'+transcript
    record={'platform':'youtube','original_id':video_id,'kind':'video','label':metadata['title'],
            'text':text,'text_field':'native_description_and_timestamped_captions','url':url,
            'published_at':metadata.get('publishDate'),'author_name':metadata.get('channelName'),
            'source':source,'raw':raw,'caption_language':language,'caption_is_auto_generated':auto,
            'caption_availability':captions['availability'],'caption_track_selection':'language-only; ambiguous kinds stay unknown',
            'declared_context':raw.get('declared_context'),'coverage':'one observed video metadata/captions; visual content and wider channel not surveyed',
            'truth_status':'source metadata and transcript; not independently verified'}
    # A display name and the old registry's channel claim cannot supply a live
    # stable author ID. Preserve them without inventing numeric identity.
    if 'channelId' in metadata:
        if not isinstance(metadata['channelId'],str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',metadata['channelId']):
            raise ValueError('collection_video_author_identity_invalid')
        record['author_id']=metadata['channelId']
    return [record],[]


def threads_post_snapshot(target: dict, data: dict, source: dict):
    """One observed post shortcode, not an assumed stable account identity."""
    if target['platform']!='threads' or target['kind']!='post':
        raise ValueError('collection_post_target_scope_required')
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('posts'),list) or len(data['posts'])!=1:
        raise ValueError('collection_incomplete_source_shape')
    raw=data['posts'][0]
    if not isinstance(raw,dict) or raw.get('id')!=target['original_id'] or not isinstance(raw.get('text'),str) or not raw['text'].strip():
        raise ValueError('collection_post_identity_or_content_invalid')
    if raw.get('availability')!='available':raise RuntimeError('collection_threads_access_not_confirmed')
    url=urlparse(raw.get('url',''))
    match=re.fullmatch(r'/@([A-Za-z0-9._]+)/post/([A-Za-z0-9_-]+)',url.path)
    if url.scheme!='https' or url.netloc!='www.threads.com' or url.query or url.fragment or not match or match[2]!=target['original_id'] or match[1]!=raw.get('author_handle'):
        raise ValueError('collection_post_url_identity_mismatch')
    if not isinstance(raw.get('coverage'),str) or not raw['coverage']:
        raise ValueError('collection_post_coverage_required')
    return [{'platform':'threads','original_id':raw['id'],'kind':'post','label':raw['text'][:80],
             'text':raw['text'],'url':raw['url'],'published_at':raw.get('published_at'),
             'author_handle':raw['author_handle'],'stable_author_id_status':'not supplied; handle is observed display identity',
             'coverage':raw['coverage'],'source':source,'raw':raw,
             'truth_status':'source post; not independently verified'}],[]


def spark_snapshot(target: dict, path: Path):
    """Online Backup of an existing derived index; never the original Kakao DB."""
    path = path.absolute()
    if any(p.is_symlink() for p in [*path.parents, path]):
        raise ValueError("collection_source_symlink")
    def source_signature():
        values=[]
        for file in [path, Path(str(path)+'-wal')]:
            if file.is_symlink():raise ValueError('collection_source_symlink')
            try:
                stat=file.stat();values.append((stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
            except FileNotFoundError:values.append(None)
        return values
    before = source_signature()
    with tempfile.TemporaryDirectory(prefix="alden-graph-snapshot-", dir="/private/tmp" if Path("/private/tmp").exists() else None) as tmp:
        snapshot = Path(tmp) / "graph.sqlite3"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.15) as original:
            original.execute("PRAGMA query_only=ON")
            with sqlite3.connect(snapshot) as dest:
                original.backup(dest, pages=512, sleep=.01)
        snapshot.chmod(0o600)
        with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=.15) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON");db.execute("BEGIN")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("collection_snapshot_integrity")
            records = []
            namespace = target["original_id"]
            for row in db.execute("SELECT id,path,title,kind,group_id,summary,provenance,hash,content_indexed FROM nodes"):
                data = dict(row)
                records.append({"platform": "graph", "original_id": namespace + ":node:" + row["id"],
                                "label": row["title"], "kind": row["kind"], "text": row["summary"] or "",
                                "source_node_id": row["id"], "source_path": row["path"], "raw": data,
                                "coverage": "existing privacy-filtered derived metadata; original bytes not opened"})
            relations = [{"source_platform": "graph", "source_id": namespace + ":node:" + row["source"],
                          "target_platform": "graph", "target_id": namespace + ":node:" + row["target"],
                          "type": row["type"], "evidence": {"derived_index": str(path), "declared_evidence": row["evidence"]}}
                         for row in db.execute("SELECT source,target,type,evidence FROM edges")]
            source = {"path": str(path), "snapshot_integrity": "ok", "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(), "observed_at": time.time()}
            if source_signature() != before:
                raise RuntimeError('collection_source_changed_during_read')
            source['mtime_ns'] = max(item[3] for item in before if item is not None)
            source['sha256'] = hashlib.sha256(encoded([records, relations])).hexdigest()
            for record in records:
                record["source"] = source
            return records, relations, source


def collect_target(store: CollectionStore, target_id: str, *, cancelled=lambda: False, publication_guard=None):
    if cancelled():
        raise RuntimeError("collection_cancelled")
    target = store.target(target_id)
    if not any(p['permission'] != 'denied' for p in target['projects']):
        raise ValueError('collection_source_scope_denied')
    config = target["config"]
    path = Path(config["path"])
    if config["adapter"] == "spark-index":
        records, relations, source = spark_snapshot(target, path)
    else:
        data, source = json_snapshot(path)
        adapter = {"source-graph": source_graph, "youtube-graph": youtube_graph, "threads-snapshot": threads_snapshot, "youtube-video":youtube_video_snapshot, "threads-post":threads_post_snapshot}[config["adapter"]]
        records, relations = adapter(target, data, source)
    # Source acquisition/validation completes before any target checkpoint.
    source_revision = source.get("sha256", source.get("snapshot_sha256"))
    order = source.get('mtime_ns')
    if order is None:
        # SQLite backup timestamps belong to a temporary copy. Order by the
        # declared source file's observed modification time instead.
        order = path.stat(follow_symlinks=False).st_mtime_ns
    result = store.ingest(target_id, records, relations=relations,
                          cursor={"source_revision": source_revision, "offset": len(records), "complete": True},
                          origin="alden-collector", cancelled=cancelled, complete_snapshot=True,
                          source_revision=source_revision, source_order=order,
                          processing_version='declared-source-snapshot-v3', publication_guard=publication_guard)
    return {"target_id": target_id, "records": len(records), "relations": len(relations),
            "source": source, "batches": [result], "coverage": "declared source snapshot, not a wider account survey"}


def capture_source(store: CollectionStore, target_id: str, *, cancelled=lambda: False):
    """Retain the permitted source bytes and replayable JSON evidence locations."""
    if cancelled():
        raise RuntimeError("collection_cancelled")
    target = store.target(target_id)
    if not target["enabled"]:
        raise RuntimeError("collection_target_paused")
    if not any(p["permission"] != "denied" for p in target["projects"]):
        raise ValueError("collection_source_scope_denied")
    cursor = json.loads(target["cursor"] or "{}")
    if not isinstance(cursor, dict) or cursor.get("complete") is not True:
        raise RuntimeError("collection_source_projection_incomplete")
    config = target["config"]; path = Path(config["path"]); adapter = config["adapter"]
    if adapter == "spark-index":
        records, relations, source = spark_snapshot(target, path)
        data = {"nodes": [r["raw"] for r in records], "relations": relations}
        raw = encoded(data)
        pointers = ["/nodes/" + str(i) for i in range(len(records))]
        scope = "exact bytes of privacy-filtered metadata export; body/protected originals excluded"
    else:
        data, source, raw = json_snapshot(path, include_raw=True)
        if source["sha256"] != cursor.get("source_revision"):
            raise RuntimeError("collection_source_revision_changed")
        handlers = {"source-graph": source_graph, "youtube-graph": youtube_graph, "threads-snapshot": threads_snapshot, "youtube-video":youtube_video_snapshot, "threads-post":threads_post_snapshot}
        records, relations = handlers[adapter](target, data, source)
        fields = ["nodes"] if adapter == "source-graph" else ["posts"] if adapter in {"threads-snapshot","threads-post"} else ["videos"] if adapter=='youtube-video' else ["series", "videos", "claims"]
        pointers = ["/" + key + "/" + str(i) for key in fields for i in range(len(data.get(key, [])))]
        scope = "exact original JSON file bytes"
    if len(records) != len(pointers):
        raise RuntimeError("collection_source_pointer_coverage")
    refs = []
    with store.database() as db:
        current_versions = {row['document_id']: (row['current_version'], row['raw_sha256']) for row in db.execute(
            'SELECT m.document_id,m.current_version,v.raw_sha256 FROM memberships m JOIN versions v ON v.id=m.current_version WHERE m.target_id=?', (target_id,))}
    for record, pointer in zip(records, pointers):
        doc = identity(record["platform"], record["original_id"])
        raw_hash = hashlib.sha256(encoded(record["raw"])).hexdigest()
        current = current_versions.get(doc)
        if current is None or current[1] != raw_hash:
            raise RuntimeError('collection_source_projection_changed')
        refs.append({"document_id": doc, "version": current[0],
                     "json_pointer": pointer, "record_sha256": raw_hash})
    manifest = {"schema": 1, "target_id": target_id, "adapter": adapter, "origin": str(path),
                "byte_scope": scope, "processing_version": "alden-source-capture-1", "records": refs}
    if adapter == "spark-index":
        manifest["current_origin_snapshot_sha256"] = source["snapshot_sha256"]
        manifest["prior_projection_origin_revision"] = cursor.get("source_revision")
        manifest["relations"] = {}
        for r in relations:
            a = identity(r["source_platform"], r["source_id"]); b = identity(r["target_platform"], r["target_id"])
            identifier = identity("relation", encoded([target_id, a, r["type"], b, r.get("original_id", "")]).decode())
            manifest["relations"][identifier] = hashlib.sha256(encoded(r["evidence"])).hexdigest()
    return store.retain_source_capture(target_id, raw, manifest, expected_cursor=target["cursor"], cancelled=cancelled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--capture-source")
    parser.add_argument("--graph", nargs="*")
    parser.add_argument("--events", action="store_true")
    parser.add_argument("--index-dense", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--after", type=int, default=0)
    args = parser.parse_args()
    choices = [args.config is not None, args.capture_source is not None, args.target is not None,
               args.graph is not None, args.events, args.index_dense, args.query is not None]
    if sum(choices) != 1:
        parser.error("select exactly one collection operation")
    if (args.index_dense or args.query is not None) and not args.project:
        parser.error("retrieval requires an explicit project scope")
    read_only = args.graph is not None or args.events or args.index_dense or args.query is not None
    store = CollectionStore.open_existing(args.state_root) if read_only else CollectionStore(args.state_root)
    if store is None:
        print(json.dumps({"state": "not_configured", "items": [], "facts": [], "nodes": [], "edges": []}))
        return
    if args.config:
        data, _ = json_snapshot(args.config)
        targets = []
        for config in data["targets"]:
            targets.append(store.register(**config))
        print(json.dumps({"registered": targets}, ensure_ascii=False))
    elif args.index_dense or args.query is not None:
        from alden_collection_retrieval import index_dense, retrieve
        import auto_reply_knowledge_graph as kg
        token = AbortToken(args.state_root / ABORT_STATE_NAME)
        with kg.embedding_abort_scope(token):
            if args.index_dense:
                os.nice(10)
                result = index_dense(args.state_root, args.project, cancelled=token.is_cancelled,
                                     progress=lambda value: print(json.dumps(value), file=__import__('sys').stderr, flush=True))
            else:
                result = retrieve(args.state_root, args.query, projects=args.project, cancelled=token.is_cancelled)
        print(json.dumps(result, ensure_ascii=False))
    elif args.capture_source:
        token = AbortToken(args.state_root / ABORT_STATE_NAME)
        print(json.dumps(capture_source(store, args.capture_source, cancelled=token.is_cancelled), ensure_ascii=False))
    elif args.target:
        token = AbortToken(args.state_root / ABORT_STATE_NAME)
        print(json.dumps(collect_target(store, args.target, cancelled=token.is_cancelled, publication_guard=token.commit_guard), ensure_ascii=False))
    elif args.graph is not None:
        print(json.dumps(store.graph(projects=args.graph), ensure_ascii=False))
    elif args.events:
        print(json.dumps(store.events(after=args.after), ensure_ascii=False))
    else:
        parser.error("select config, target, graph or events")


if __name__ == "__main__":
    main()
