"""Fetch pinned public MIRACL Korean files once; keep native relevance labels."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import gzip
import hashlib
import json
import os
import time
import urllib.request

sha = lambda raw: hashlib.sha256(raw).hexdigest()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
ROOT = args.output_dir.resolve()
assert ROOT.is_relative_to(Path.cwd() / 'outputs'), 'Export only to owned outputs under the working directory'
ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
assert not (ROOT/'selected-corpus.jsonl').exists(), 'Read existing result before repeating extraction'
DATASET_REVISION = '5be20db9509754dadad47689368639fcec739c00'
CORPUS_REVISION = 'd921ec7e349ce0d28daf30b2da9da5ee698bef0d'
PINS = {
    'docs-0.jsonl.gz': (87965596, 'c56a31883a291504aa9c97968fb7a5fbcc9ea1099ee1810200249e1afb7fc55d'),
    'docs-1.jsonl.gz': (75422723, 'fecd2886124e78c3aa87c59604ac9909c23165415c70a96f4edb95677b5ed0cd'),
    'docs-2.jsonl.gz': (62582229, '93e90d098d78f50a9e76dd2e64608d65e0563787954f848da9b04286f81b75d9'),
}
for kind, expected in (
    ('topics', 'a365552cfc997a8915e948a3b5994883f641e7c3f12b6af87bbfaf89729e32a8'),
    ('qrels', '88827ccc5b64e531b25f70eef30202d33daa9bfa3ac8cceadf5cdbd5ca5034df'),
):
    name = kind + '.miracl-v1.0-ko-dev.tsv'
    path = ROOT / name
    if path.exists():
        assert sha(path.read_bytes()) == expected
    else:
        url = 'https://huggingface.co/datasets/miracl/miracl/resolve/' + DATASET_REVISION + '/miracl-v1.0-ko/' + kind + '/' + name
        data = urllib.request.urlopen(url, timeout=30).read(1024 * 1024 + 1)
        assert len(data) <= 1024 * 1024 and sha(data) == expected
        path.write_bytes(data); path.chmod(0o600)
tree_path = ROOT / 'corpus-tree-ko.json'
if not tree_path.exists():
    url = 'https://huggingface.co/api/datasets/miracl/miracl-corpus/tree/' + CORPUS_REVISION + '/miracl-corpus-v1.0-ko'
    data = urllib.request.urlopen(url, timeout=30).read(256 * 1024 + 1)
    assert len(data) <= 256 * 1024
    tree_path.write_bytes(data); tree_path.chmod(0o600)
(ROOT / 'source-revision.txt').write_text(DATASET_REVISION + '\n')
(ROOT / 'corpus-revision.txt').write_text(CORPUS_REVISION + '\n')
tree = json.loads((ROOT / 'corpus-tree-ko.json').read_text())
revision = (ROOT / 'corpus-revision.txt').read_text().strip()
assert len(tree) == 3 and sum(f['size'] for f in tree) < 256 * 1024 * 1024
assert {Path(f['path']).name: (f['size'], f['lfs']['oid']) for f in tree} == PINS
assert all(f['path'] == 'miracl-corpus-v1.0-ko/' + Path(f['path']).name for f in tree)
assert not (ROOT / 'selected-corpus.jsonl').exists(), 'Read existing result before repeating extraction'
topics = {}
for line in (ROOT / 'topics.miracl-v1.0-ko-dev.tsv').read_text().splitlines():
    qid, text = line.split('\t', 1)
    assert qid not in topics and text.strip()
    topics[qid] = text
qrels = {}
for line in (ROOT / 'qrels.miracl-v1.0-ko-dev.tsv').read_text().splitlines():
    qid, unused, docid, relevance = line.split()
    assert qid in topics and relevance in {'0', '1'}
    assert docid not in qrels.setdefault(qid, {})
    qrels[qid][docid] = int(relevance)
assert len(topics) == 213 and set(topics) == set(qrels)
assert all(any(judgments.values()) for judgments in qrels.values())
wanted = {docid for judgments in qrels.values() for docid in judgments}

def download(entry):
    path = ROOT / Path(entry['path']).name
    if path.exists():
        assert path.stat().st_size == entry['size'] and sha(path.read_bytes()) == entry['lfs']['oid']
        return path
    partial = path.with_suffix(path.suffix + '.part')
    assert not partial.exists(), 'Preserve the partial download; reconcile before retry'
    url = 'https://huggingface.co/datasets/miracl/miracl-corpus/resolve/' + revision + '/' + entry['path']
    size = 0
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=30) as response, partial.open('xb') as handle:
        partial.chmod(0o600)
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            assert size <= entry['size']
            digest.update(chunk)
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    assert size == entry['size'] and digest.hexdigest() == entry['lfs']['oid']
    partial.rename(path)
    print(json.dumps({'downloaded': path.name, 'bytes': size, 'sha256': digest.hexdigest()}), flush=True)
    return path

with ThreadPoolExecutor(max_workers=3) as pool:
    archives = list(pool.map(download, tree))

found = {}
counts = {}
decompressed = 0
with (ROOT / 'selected-corpus.jsonl').open('x') as selected:
    (ROOT / 'selected-corpus.jsonl').chmod(0o600)
    for archive in archives:
        count = 0
        with gzip.open(archive, 'rb') as handle:
            while line := handle.readline(4 * 1024 * 1024 + 1):
                assert len(line) <= 4 * 1024 * 1024
                decompressed += len(line)
                assert decompressed <= 4 * 1024 * 1024 * 1024
                value = json.loads(line)
                count += 1
                docid = value['docid']
                if docid in wanted:
                    assert docid not in found
                    assert isinstance(value['title'], str) and isinstance(value['text'], str) and value['text'].strip()
                    found[docid] = {'archive': archive.name, 'decompressed_line': count, 'line_sha256': sha(line)}
                    selected.write(line.decode())
        counts[archive.name] = count
        print(json.dumps({'archive_parsed': archive.name, 'rows': count, 'matched': len(found)}), flush=True)
assert set(found) == wanted, ('missing', sorted(wanted - set(found)))
assert sum(counts.values()) == 1486752
result = {'dataset': 'MIRACL Korean dev', 'dataset_revision': (ROOT / 'source-revision.txt').read_text().strip(),
          'corpus_revision': revision, 'queries': topics, 'qrels': qrels, 'source_records': found,
          'query_count': len(topics), 'judgment_count': sum(map(len, qrels.values())),
          'selected_documents': len(found), 'full_corpus_passages': sum(counts.values()),
          'archive_counts': counts, 'decompressed_bytes': decompressed,
          'selected_corpus_sha256': sha((ROOT / 'selected-corpus.jsonl').read_bytes()),
          'source_files_sha256': {name: sha((ROOT / name).read_bytes()) for name in
              ['topics.miracl-v1.0-ko-dev.tsv', 'qrels.miracl-v1.0-ko-dev.tsv', 'corpus-tree-ko.json']},
          'scope': 'All213 Korean dev queries, all official3057 judgments; selected judged-passage union, not a full1.48M-document index',
          'license_note': 'Hub cards declare Apache-2.0; underlying Wikipedia attribution/share-alike remains separately applicable. No corpus text is committed or republished.',
          'prepared_at': time.time()}
(ROOT / 'selection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
(ROOT / 'selection.json').chmod(0o600)
print(json.dumps({k: result[k] for k in ['query_count', 'judgment_count', 'selected_documents', 'full_corpus_passages', 'decompressed_bytes', 'selected_corpus_sha256']}), flush=True)
