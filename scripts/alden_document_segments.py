"""Bounded structural evidence from supplied document bytes, without retrieval.

No links, formulas, macros or document instructions are executed. Native readers
are available only through the caller's bounded run_process adapter. The chat
attachment extractor is intentionally unchanged. Locations refer to source
structure, except explicitly labelled textutil converted lines.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib

from alden_file_content import (
    FileContentUnavailable, MAX_FILE_BYTES, MAX_TEXT_BYTES, MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_ENTRIES, MAX_DOCUMENT_PAGES, TEXT_SUFFIXES, _archive, _xml_text,
)

PROCESSING_VERSION = "document-segments-v1"
MAX_SEGMENTS = 256
MAX_XML_NODES = 250_000
_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_NATIVE_LIMITS = {"timeout": 8.0, "stdout_cap": 256_000, "stderr_cap": 4096}


def _check(cancelled):
    if cancelled():
        raise FileContentUnavailable("document_cancelled")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_text(text):
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in text):
        raise FileContentUnavailable("document_contains_binary_text")
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise FileContentUnavailable("document_encoding_unavailable") from None


def _decode(data, *, xml=False):
    try:
        return data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeError:
        raise FileContentUnavailable("document_xml_encoding_unsupported" if xml
                                     else "document_encoding_unavailable") from None


def _no_entities(source):
    if "\x00" in source:
        raise FileContentUnavailable("document_xml_encoding_unsupported")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source, re.I):
        raise FileContentUnavailable("document_xml_entities_unsupported")


def _xml(data, cancelled, node_budget=None):
    """Incremental stdlib XML parse with cancellation and a shared node budget."""
    source = _decode(data, xml=True)
    _no_entities(source)
    budget = node_budget if node_budget is not None else [0]
    parser = ET.XMLPullParser(events=("start",))
    root = None
    try:
        for offset in range(0, len(source), 8192):
            _check(cancelled)
            parser.feed(source[offset:offset + 8192])
            for _, element in parser.read_events():
                budget[0] += 1
                if budget[0] > MAX_XML_NODES:
                    raise FileContentUnavailable("document_xml_node_budget")
                if root is None:
                    root = element
        parser.close()
    except ET.ParseError:
        raise FileContentUnavailable("document_xml_invalid") from None
    if root is None:
        raise FileContentUnavailable("document_xml_invalid")
    _check(cancelled)
    return root


class _Segments:
    def __init__(self, cancelled):
        self.cancelled = cancelled
        self.items = []
        self.used = 0
        self.seen = 0
        self.omitted = 0
        self.partial = None
        self.truncated = False
        self.closed = False

    def add(self, location, text):
        _check(self.cancelled)
        self.seen += 1
        if self.closed or len(self.items) >= MAX_SEGMENTS:
            self.omitted += 1
            self.closed = self.truncated = True
            return
        # Slice before encoding: even a repeated huge shared string is bounded.
        remaining = MAX_TEXT_BYTES - self.used - bool(self.items)
        if remaining < 0 or (remaining == 0 and text):
            self.omitted += 1
            self.closed = self.truncated = True
            return
        prefix = text[:remaining + 1]
        _valid_text(prefix)
        encoded = prefix.encode("utf-8")
        clipped = len(text) > len(prefix) or len(encoded) > remaining
        value = encoded[:remaining].decode("utf-8", "ignore")
        if clipped and not value:
            self.omitted += 1
            self.closed = self.truncated = True
            return
        self.items.append({"location": dict(location), "text": value, "text_sha256": _sha(value)})
        self.used += len(value.encode("utf-8")) + (1 if len(self.items) > 1 else 0)
        if clipped:
            self.partial = len(self.items)
            self.closed = self.truncated = True

    def finish(self, parser, *, body_complete=False, **scope):
        _check(self.cancelled)
        text = "\n".join(x["text"] for x in self.items)
        if not text.strip():
            raise FileContentUnavailable("document_text_unavailable_within_budget" if self.truncated
                                         else "document_has_no_text")
        scope.update(segment_candidates_seen=self.seen, segments_omitted=self.omitted,
                     partial_segment=self.partial, max_segments=MAX_SEGMENTS,
                     max_text_bytes=MAX_TEXT_BYTES, joined_with="LF")
        truncated = self.truncated or scope.get("structure_omitted", False)
        # Coverage is a separate assertion from exhausting the extraction budget.
        # Only readers which cover the original textual body may opt in.
        scope["body_complete"] = body_complete is True and not truncated
        return {"processing_version": PROCESSING_VERSION, "parser": parser,
                "text": text, "text_sha256": _sha(text),
                "truncated": truncated,
                "scope": scope, "segments": self.items}


def _lines(text, suffix, cancelled, *, converted=False):
    _valid_text(text)
    result = _Segments(cancelled)
    for number, line in enumerate(io.StringIO(text, newline=None), 1):
        result.add({"kind": "converted_line" if converted else "line",
                    "line_start": number, "line_end": number},
                   line[:-1] if line.endswith("\n") else line)
    return result.finish("macos-textutil" if converted else "text-" + suffix[1:],
                         kind="converted-lines" if converted else "original-lines",
                         original_positions=not converted, line_endings="LF-normalized",
                         body_complete=not converted,
                         body_coverage=("textutil conversion; original text coverage not proven" if converted
                                        else "all decoded source lines, subject to extraction limits"),
                         lines_total=result.seen, segment_count_complete=True)


def _records(text, suffix, cancelled):
    _valid_text(text)
    result = _Segments(cancelled)

    def physical_lines():
        for line in io.StringIO(text, newline=""):
            _check(cancelled)
            yield line

    reader = csv.reader(physical_lines(), delimiter="," if suffix == ".csv" else "\t", strict=True)
    end = 0
    try:
        for index, row in enumerate(reader, 1):
            _check(cancelled)
            start, end = end + 1, reader.line_num
            rendered = ""
            if not result.closed and len(result.items) < MAX_SEGMENTS:
                output = io.StringIO(newline="")
                csv.writer(output, delimiter="," if suffix == ".csv" else "\t",
                           lineterminator="\n").writerow(row)
                rendered = output.getvalue()[:-1]
            result.add({"kind": "record", "record_index": index,
                        "line_start": start, "line_end": end}, rendered)
    except csv.Error as error:
        code = "document_csv_field_budget" if "field larger than field limit" in str(error) else "document_csv_invalid"
        raise FileContentUnavailable(code) from None
    return result.finish("csv-records" if suffix == ".csv" else "tsv-records",
                         kind="original-records", rendering="csv-writer",
                         body_complete=True,
                         body_coverage="all decoded records and fields, subject to extraction limits",
                         records_total=result.seen, physical_lines_total=end,
                         segment_count_complete=True)


def _safe_member(name):
    return (isinstance(name, str) and 0 < len(name) <= 1024 and not name.startswith("/")
            and not any(c in name for c in "\\:%?#\x00")
            and not any(ord(c) < 32 for c in name)
            and all(p not in {"", ".", ".."} for p in name.rstrip("/").split("/")))


def _relations(root, member, names, cancelled):
    if _local(root.tag) != "Relationships":
        raise FileContentUnavailable("document_archive_relation_invalid")
    base = "" if member == "_rels/.rels" else posixpath.dirname(posixpath.dirname(member))
    relations = {}
    for entry in root:
        _check(cancelled)
        rid, target = entry.get("Id", ""), entry.get("Target", "")
        if (_local(entry.tag) != "Relationship" or not 0 < len(rid) <= 256 or rid in relations
                or not 0 < len(target) <= 1024):
            raise FileContentUnavailable("document_archive_relation_invalid")
        if (entry.get("TargetMode", "Internal") != "Internal" or target.startswith("//")
                or any(c in target for c in "\\:%?#\x00")
                or any(ord(c) < 32 for c in target)):
            raise FileContentUnavailable("document_archive_relation_unsafe")
        resolved = posixpath.normpath(target[1:] if target.startswith("/") else posixpath.join(base, target))
        if not _safe_member(resolved) or resolved not in names or resolved.endswith("/"):
            raise FileContentUnavailable("document_archive_relation_unsafe")
        relations[rid] = resolved
    return relations


def _preflight(archive, cancelled):
    names = set(archive.namelist())
    for info in archive.infolist():
        _check(cancelled)
        if (not _safe_member(info.filename) or stat.S_ISLNK(info.external_attr >> 16)
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise FileContentUnavailable("document_archive_unsafe")
    relations, nodes = {}, [0]
    # Validate even omitted XML parts and relations before accepting partial text.
    for name in sorted(names):
        _check(cancelled)
        if name.lower().endswith((".xml", ".rels")):
            root = _xml(archive.read(name), cancelled, nodes)
            if name.lower().endswith(".rels"):
                if name != "_rels/.rels" and "/_rels/" not in name:
                    raise FileContentUnavailable("document_archive_relation_invalid")
                relations[name] = _relations(root, name, names, cancelled)
    return names, relations


def _docx(archive, names, cancelled):
    if "word/document.xml" not in names:
        raise FileContentUnavailable("document_format_mismatch")
    parts = ["word/document.xml"] + sorted(n for n in names if re.fullmatch(
        r"word/(?:footnotes|endnotes|header\d+|footer\d+)\.xml", n))
    result = _Segments(cancelled)
    for part in parts:
        root = _xml(archive.read(part), cancelled)
        if _local(root.tag) != ("document" if part == "word/document.xml" else
                               re.sub(r"\d+$", "", Path(part).stem).replace("header", "hdr").replace("footer", "ftr")):
            raise FileContentUnavailable("document_docx_structure_invalid")
        paragraph = 0
        for element in root.iter():
            _check(cancelled)
            if _local(element.tag) == "p":
                paragraph += 1
                result.add({"kind": "paragraph", "part": part, "paragraph": paragraph},
                           "" if result.closed else _paragraph_text(element, cancelled))
    return result.finish("docx-xml", kind="xml-part-paragraphs", parts_read=parts,
                         body_coverage="selected Word paragraphs; comments, alternative chunks and other text not certified",
                         parts_without_text_extraction=sorted(n for n in names if n.endswith(".xml") and n not in parts),
                         pages_known=False, segment_count_complete=True)


def _paragraph_text(root, cancelled):
    # A text box can contain paragraphs inside another paragraph. Its text belongs
    # to those inner structural positions, not to both the outer and inner p.
    parts, stack = [], list(reversed(list(root)))
    while stack:
        _check(cancelled)
        element = stack.pop()
        tag = _local(element.tag)
        if tag in {"p", "pPr", "rPr"}:
            continue
        if tag == "t":
            parts.append(element.text or "")
        elif tag == "tab":
            parts.append("\t")
        elif tag in {"br", "cr"}:
            parts.append("\n")
        stack.extend(reversed(list(element)))
    return "".join(parts)


def _ordered_parts(archive, names, relations, suffix, cancelled):
    is_sheet = suffix == ".xlsx"
    root_part = "xl/workbook.xml" if is_sheet else "ppt/presentation.xml"
    rel_part = "xl/_rels/workbook.xml.rels" if is_sheet else "ppt/_rels/presentation.xml.rels"
    if root_part not in names or rel_part not in relations:
        raise FileContentUnavailable("document_format_mismatch")
    root = _xml(archive.read(root_part), cancelled)
    if _local(root.tag) != ("workbook" if is_sheet else "presentation"):
        raise FileContentUnavailable("document_office_structure_invalid")
    entries, ids, targets = [], set(), set()
    for entry in root.iter():
        _check(cancelled)
        if _local(entry.tag) != ("sheet" if is_sheet else "sldId"):
            continue
        rid = entry.get(_REL_ID)
        target = relations[rel_part].get(rid)
        pattern = r"xl/worksheets/[^/]+\.xml" if is_sheet else r"ppt/slides/[^/]+\.xml"
        identity = entry.get("sheetId" if is_sheet else "id", "")
        if (target is None or not re.fullmatch(pattern, target) or target in targets
                or not re.fullmatch(r"[0-9]{1,10}", identity) or not 0 < int(identity) <= 2**32 - 1
                or int(identity) in ids):
            raise FileContentUnavailable("document_sheet_relation_invalid" if is_sheet else "document_slide_relation_invalid")
        name = entry.get("name", "")
        if is_sheet and (not 0 < len(name) <= 31 or any(x.get("name") == name for _, x in entries)):
            raise FileContentUnavailable("document_sheet_identity_invalid")
        targets.add(target)
        ids.add(int(identity))
        entries.append((target, dict(entry.attrib)))
    if not entries:
        raise FileContentUnavailable("document_has_no_text")
    return entries


def _pptx(archive, names, relations, cancelled):
    slides = _ordered_parts(archive, names, relations, ".pptx", cancelled)
    result = _Segments(cancelled)
    notes_seen, notes_read, parts_read = set(), [], []
    for index, (part, entry) in enumerate(slides, 1):
        rel_part = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        # Use the validated slide links, never ZIP order or notes filename numbers.
        notes = []
        for target in relations.get(rel_part, {}).values():
            _check(cancelled)
            if re.fullmatch(r"ppt/notesSlides/[^/]+\.xml", target):
                notes.append(target)
        if len(notes) > 1 or any(note in notes_seen for note in notes):
            raise FileContentUnavailable("document_notes_relation_invalid")
        notes_seen.update(notes)
        if index > MAX_DOCUMENT_PAGES:
            result.closed = True
        for text_part in [part] + notes:
            is_notes = text_part != part
            root = _xml(archive.read(text_part), cancelled)
            if _local(root.tag) != ("notes" if is_notes else "sld"):
                raise FileContentUnavailable("document_notes_structure_invalid" if is_notes
                                             else "document_slide_structure_invalid")
            if index <= MAX_DOCUMENT_PAGES:
                parts_read.append(text_part)
                if is_notes:
                    notes_read.append(text_part)
            paragraph = 0
            for element in root.iter():
                _check(cancelled)
                if _local(element.tag) == "p":
                    paragraph += 1
                    result.add({"kind": "paragraph", "part": text_part, "slide": index,
                                "slide_id": entry["id"], "paragraph": paragraph},
                               "" if result.closed else _paragraph_text(element, cancelled))
    read = min(len(slides), MAX_DOCUMENT_PAGES)
    emitted = len({x["location"]["slide"] for x in result.items})
    emitted_parts = {x["location"]["part"] for x in result.items}
    return result.finish("pptx-xml", kind="slide-paragraphs", slides_read=read,
                         slides_total=len(slides), slides_omitted=len(slides) - read,
                         slides_with_segments=emitted, slides_without_segments=len(slides) - emitted,
                         structure_omitted=read < len(slides), segment_count_complete=True,
                         segment_count_scope="ordered slides and their linked notes",
                         notes_read=bool(notes_read), notes_parts_read=notes_read, parts_read=parts_read,
                         notes_parts_omitted=sorted(n for n in names if re.fullmatch(
                             r"ppt/notesSlides/[^/]+\.xml", n) and n not in notes_read),
                         notes_parts_without_segments=sorted(notes_seen - emitted_parts),
                         body_coverage="ordered slide and linked notes paragraphs; charts, diagrams, masters and other text not certified",
                         parts_without_text_extraction=sorted(n for n in names if n.endswith(".xml") and n not in parts_read))


def _uint(value, code, *, maximum=2**31 - 1, minimum=0):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value) or not minimum <= int(value) <= maximum:
        raise FileContentUnavailable(code)
    return int(value)


def _cell_coordinate(value):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", value)
    if not match:
        raise FileContentUnavailable("document_cell_coordinate_invalid")
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    if column > 16_384 or int(match[2]) > 1_048_576:
        raise FileContentUnavailable("document_cell_coordinate_invalid")
    return int(match[2])


def _xlsx(archive, names, relations, cancelled):
    sheets = _ordered_parts(archive, names, relations, ".xlsx", cancelled)
    strings = []
    if "xl/sharedStrings.xml" in names:
        root = _xml(archive.read("xl/sharedStrings.xml"), cancelled)
        if _local(root.tag) != "sst":
            raise FileContentUnavailable("document_shared_string_invalid")
        for entry in root:
            _check(cancelled)
            if _local(entry.tag) != "si":
                raise FileContentUnavailable("document_shared_string_invalid")
            strings.append(_xml_text(entry))
    result = _Segments(cancelled)
    for index, (part, sheet) in enumerate(sheets, 1):
        if index > MAX_DOCUMENT_PAGES:
            result.closed = True
        root = _xml(archive.read(part), cancelled)
        if _local(root.tag) != "worksheet":
            raise FileContentUnavailable("document_sheet_structure_invalid")
        coordinates = set()
        for row in root.iter():
            _check(cancelled)
            if _local(row.tag) != "row":
                continue
            row_index = (_uint(row.get("r"), "document_cell_coordinate_invalid", minimum=1, maximum=1_048_576)
                         if "r" in row.attrib else None)
            for cell in row:
                _check(cancelled)
                if _local(cell.tag) != "c":
                    continue
                coordinate = cell.get("r", "")
                actual_row = _cell_coordinate(coordinate)
                if coordinate in coordinates or (row_index is not None and row_index != actual_row):
                    raise FileContentUnavailable("document_cell_coordinate_invalid")
                coordinates.add(coordinate)
                if "s" in cell.attrib:
                    _uint(cell.get("s"), "document_cell_style_invalid", maximum=MAX_XML_NODES)
                kind = cell.get("t", "n")
                if kind not in {"n", "s", "str", "inlineStr", "b", "d", "e"}:
                    raise FileContentUnavailable("document_cell_type_invalid")
                values = [x for x in cell if _local(x.tag) == "v"]
                formulas = [x for x in cell if _local(x.tag) == "f"]
                if len(values) > 1 or len(formulas) > 1:
                    raise FileContentUnavailable("document_cell_structure_invalid")
                value = (values[0].text or "") if values else ""
                if kind == "s":
                    string_index = _uint(value, "document_shared_string_invalid", maximum=len(strings) - 1)
                    value = strings[string_index]
                elif kind == "inlineStr":
                    value = _xml_text(cell)
                formula = formulas[0] if formulas else None
                formula_kind = formula.get("t", "normal") if formula is not None else None
                if formula is not None:
                    if formula_kind not in {"normal", "shared", "array", "dataTable"}:
                        raise FileContentUnavailable("document_formula_invalid")
                    if "si" in formula.attrib:
                        _uint(formula.get("si"), "document_formula_invalid", maximum=MAX_XML_NODES)
                if formula is None and not value:
                    continue
                text = ""
                if not result.closed:
                    text = value
                    if formula is not None:
                        # JSON distinguishes a missing cache from a literal string.
                        # Slice each component before serialization; budget discloses any clipping.
                        raw_formula = formula.text or ""
                        text = json.dumps({"formula": raw_formula[:MAX_TEXT_BYTES + 1],
                                           "cached_value": value[:MAX_TEXT_BYTES + 1] if values else None},
                                          ensure_ascii=False)
                result.add({"kind": "cell", "part": part, "sheet_index": index,
                            "sheet_id": sheet["sheetId"], "sheet_name": sheet["name"],
                            "cell": coordinate, "cell_type": kind,
                            "formula_present": formula is not None, "formula_kind": formula_kind,
                            "cached_value_present": bool(values)}, text)
    read = min(len(sheets), MAX_DOCUMENT_PAGES)
    emitted = len({x["location"]["sheet_index"] for x in result.items})
    extracted_parts = {part for part, _ in sheets[:read]}
    return result.finish("xlsx-xml", kind="sheet-cells", sheets_read=read,
                         sheets_total=len(sheets), sheets_omitted=len(sheets) - read,
                         sheets_with_segments=emitted, sheets_without_segments=len(sheets) - emitted,
                         structure_omitted=read < len(sheets), segment_count_complete=True,
                         body_coverage="ordered worksheet cell values and formulas; comments, drawings and other text not certified",
                         parts_without_text_extraction=sorted(n for n in names if n.endswith(".xml")
                                                             and n not in extracted_parts),
                         formulas_evaluated=False, number_formats_applied=False,
                         formula_rendering="json-formula-cached_value", cached_values_may_be_stale=True)


def _office(data, suffix, cancelled):
    try:
        with _archive(data) as archive:
            names, relations = _preflight(archive, cancelled)
            if suffix == ".docx":
                return _docx(archive, names, cancelled)
            if suffix == ".pptx":
                return _pptx(archive, names, relations, cancelled)
            return _xlsx(archive, names, relations, cancelled)
    except FileContentUnavailable:
        raise
    except (KeyError, OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile,
            zlib.error, ValueError, OverflowError):
        raise FileContentUnavailable("document_archive_invalid") from None


_PDF_SCRIPT = r'''function run(args) {
    try {
        ObjC.import('PDFKit');
        var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath($(args[0])));
        if (doc.isNil()) return JSON.stringify({status:'malformed'});
        if (doc.isLocked) return JSON.stringify({status:'locked'});
        var total = Number(doc.pageCount), pages = [], used = 0, clipped = false;
        if (total < 1) return JSON.stringify({status:'empty'});
        for (var i=0; i<Math.min(total,50); i++) {
            var page = doc.pageAtIndex(i);
            if (page.isNil()) return JSON.stringify({status:'malformed'});
            var value = page.string, text = value.isNil() ? '' : ObjC.unwrap(value);
            var limit = 32000-used, cut = text.length > limit;
            text = text.slice(0,limit);
            if (cut && text.length && /[\uD800-\uDBFF]/.test(text.slice(-1))) text=text.slice(0,-1);
            pages.push({page:i+1,text:text,truncated:cut});
            used += text.length;
            if (cut || used >= 32000) { clipped = cut; break; }
        }
        return JSON.stringify({status:'ok',pages_total:total,pages:pages,
                               truncated:clipped || pages.length<total});
    } catch (_) { return JSON.stringify({status:'unavailable'}); }
}'''


def _native_output(data, suffix, run_process, cancelled):
    if run_process is None:
        raise FileContentUnavailable("document_native_parser_unavailable")
    magic = {".pdf": b"%PDF-", ".rtf": b"{\\rtf", ".doc": bytes.fromhex("d0cf11e0a1b11ae1")}
    if not data.startswith(magic[suffix]):
        raise FileContentUnavailable("document_format_mismatch")
    _check(cancelled)
    try:
        with tempfile.TemporaryDirectory(prefix="alden-segments-", dir=os.environ.get("ALDEN_TEST_TMPDIR")) as td:
            Path(td).chmod(0o700)
            path = Path(td) / ("source" + suffix)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o400)
            command = (["/usr/bin/osascript", "-l", "JavaScript", "-e", _PDF_SCRIPT, "--", str(path)]
                       if suffix == ".pdf" else ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)])
            _check(cancelled)
            try:
                response = run_process(command, **_NATIVE_LIMITS)
            except FileContentUnavailable:
                # A callback's exception message is not a trusted public code.
                raise FileContentUnavailable("document_native_parser_failed") from None
            _check(cancelled)
            if (not isinstance(response, (tuple, list)) or len(response) != 3
                    or type(response[0]) is not int or not isinstance(response[1], bytes)
                    or not isinstance(response[2], bytes)):
                raise FileContentUnavailable("document_native_response_invalid")
            code, stdout, stderr = response
            if len(stdout) > _NATIVE_LIMITS["stdout_cap"] or len(stderr) > _NATIVE_LIMITS["stderr_cap"]:
                raise FileContentUnavailable("document_native_output_budget")
            if code != 0:
                raise FileContentUnavailable("document_native_parser_failed")
            return stdout
    except FileContentUnavailable:
        raise
    except (TimeoutError, subprocess.TimeoutExpired):
        raise FileContentUnavailable("document_native_parser_timeout") from None
    except Exception:
        raise FileContentUnavailable("document_native_parser_failed") from None


def _pdf(stdout, cancelled):
    try:
        payload = json.loads(stdout)
    except (ValueError, UnicodeError, RecursionError):
        raise FileContentUnavailable("document_pdf_response_invalid") from None
    if not isinstance(payload, dict):
        raise FileContentUnavailable("document_pdf_response_invalid")
    status = payload.get("status")
    failures = {"locked": "document_pdf_locked", "malformed": "document_pdf_invalid",
                "empty": "document_pdf_empty", "unavailable": "document_pdf_unavailable"}
    if isinstance(status, str) and status in failures:
        raise FileContentUnavailable(failures[status])
    total, pages, clipped = payload.get("pages_total"), payload.get("pages"), payload.get("truncated")
    if (status != "ok" or type(total) is not int or not 1 <= total <= 2**31 - 1
            or not isinstance(pages, list) or not 1 <= len(pages) <= min(total, MAX_DOCUMENT_PAGES)
            or type(clipped) is not bool):
        raise FileContentUnavailable("document_pdf_response_invalid")
    result, native_cut = _Segments(cancelled), False
    for index, page in enumerate(pages, 1):
        _check(cancelled)
        if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] != index
                or not isinstance(page.get("text"), str) or type(page.get("truncated")) is not bool
                or (page["truncated"] and index != len(pages))):
            raise FileContentUnavailable("document_pdf_response_invalid")
        _valid_text(page["text"])
        native_cut = native_cut or page["truncated"]
        result.add({"kind": "page", "page": page["page"]}, page["text"])
    if clipped != (native_cut or len(pages) < total):
        raise FileContentUnavailable("document_pdf_response_invalid")
    if not any(p["text"].strip() for p in pages):
        raise FileContentUnavailable("document_pdf_has_no_text")
    return result.finish("macos-pdfkit", kind="pdf-pages", pages_read=len(pages),
                         pages_total=total, pages_omitted=total - len(pages),
                         pages_with_segments=len(result.items), pages_without_segments=total - len(result.items),
                         native_text_truncated=native_cut, structure_omitted=clipped,
                         body_coverage="PDFKit page text only; image, annotation and form text coverage not proven",
                         segment_count_complete=len(pages) == total, ocr_performed=False)


def parse_document_segments(data: bytes, filename: str, *, run_process=None, cancelled=lambda: False) -> dict:
    """Return a fresh seven-field result, or a static FileContentUnavailable code.

    Limits include 5 MiB input, the existing archive limits, 50 PDF pages / Office
    sheets or slides, 250,000 XML nodes, 256 segments and 16,000 UTF-8 bytes of
    joined text. CSV uses the stdlib reader's existing field-size safety limit;
    this parser never changes that process-global setting. No native subprocess
    is launched unless the caller supplies run_process(command, timeout=8.0,
    stdout_cap=256000, stderr_cap=4096). Scope body_complete is a strict boolean
    about original textual coverage, independent of an untruncated extraction.
    Office XML parts, PDF page text and native conversions are conservatively
    incomplete even when their selected extraction scope fits the limits.
    """
    _check(cancelled)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES:
        raise FileContentUnavailable("document_byte_budget")
    if not isinstance(filename, str) or not filename or len(filename) > 4096 or "\x00" in filename:
        raise FileContentUnavailable("document_filename_invalid")
    suffix = Path(filename).suffix.lower()
    if suffix in {".docx", ".xlsx", ".pptx"}:
        return _office(data, suffix, cancelled)
    if suffix in {".pdf", ".rtf", ".doc"}:
        stdout = _native_output(data, suffix, run_process, cancelled)
        if suffix == ".pdf":
            return _pdf(stdout, cancelled)
        return _lines(_decode(stdout), suffix, cancelled, converted=True)
    if suffix not in TEXT_SUFFIXES:
        raise FileContentUnavailable("document_format_unsupported")
    text = _decode(data)
    _check(cancelled)
    if suffix in {".xml", ".html", ".htm"}:
        _no_entities(text)
        if suffix == ".xml":
            _xml(data, cancelled)
    if suffix in {".csv", ".tsv"}:
        return _records(text, suffix, cancelled)
    return _lines(text, suffix, cancelled)
