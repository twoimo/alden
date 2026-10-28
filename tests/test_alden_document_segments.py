"""Isolated synthetic fixtures; native calls are injected stubs, never executed."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import alden_document_segments as parser
import alden_file_content as legacy

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PDF = b"%PDF-1.7\nsynthetic injected-reader fixture\n"


def archive(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in (parts.items() if isinstance(parts, dict) else parts):
            z.writestr(name, data)
    return output.getvalue()


def paragraph(value, prefix="w"):
    return f'<{prefix}:p><{prefix}:r><{prefix}:t xml:space="preserve">{escape(value)}</{prefix}:t></{prefix}:r></{prefix}:p>'


def docx_parts(values=("검수 42개",)):
    return {"word/document.xml": f'<w:document xmlns:w="{W}"><w:body>' +
            "".join(paragraph(value) for value in values) + '</w:body></w:document>'}


def xlsx_parts(sheets=None, strings=("올든 42개",)):
    sheets = sheets if sheets is not None else [("수량", "7", '<x:row r="3"><x:c r="C3" t="s"><x:v>0</x:v></x:c></x:row>')]
    parts = {
        "xl/workbook.xml": f'<x:workbook xmlns:x="{X}" xmlns:r="{R}"><x:sheets>' + "".join(
            f'<x:sheet name="{name}" sheetId="{identity}" r:id="r{i}"/>'
            for i, (name, identity, _) in enumerate(sheets, 1)) + '</x:sheets></x:workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships>' + "".join(
            f'<Relationship Id="r{i}" Target="worksheets/data{i}.xml"/>'
            for i in range(1, len(sheets) + 1)) + '</Relationships>',
        "xl/sharedStrings.xml": f'<x:sst xmlns:x="{X}">' + "".join(
            f'<x:si><x:t xml:space="preserve">{escape(value)}</x:t></x:si>' for value in strings) + '</x:sst>',
    }
    for i, (_, _, cells) in enumerate(sheets, 1):
        parts[f"xl/worksheets/data{i}.xml"] = f'<x:worksheet xmlns:x="{X}"><x:sheetData>{cells}</x:sheetData></x:worksheet>'
    return parts


def pptx_parts(values=(("first",), ("last",)), order=None):
    order = order if order is not None else list(range(len(values), 0, -1))
    parts = {
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>' + "".join(
            f'<p:sldId id="{256 + i}" r:id="r{slide}"/>' for i, slide in enumerate(order)) + '</p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships>' + "".join(
            f'<Relationship Id="r{i}" Target="slides/slide{i}.xml"/>' for i in range(1, len(values) + 1)) + '</Relationships>',
    }
    for i, texts in enumerate(values, 1):
        parts[f"ppt/slides/slide{i}.xml"] = f'<p:sld xmlns:p="{P}" xmlns:a="{A}">' + "".join(
            paragraph(text, "a") for text in texts) + '</p:sld>'
    return parts


def link_notes(parts, slide, values, *, name=None):
    part = f"ppt/notesSlides/{name or ('notesSlide' + str(slide) + '.xml')}"
    parts[f"ppt/slides/_rels/slide{slide}.xml.rels"] = (
        '<Relationships><Relationship Id="notes" Type="' + R + '/notesSlide" '
        f'Target="../notesSlides/{Path(part).name}"/></Relationships>')
    parts[part] = f'<p:notes xmlns:p="{P}" xmlns:a="{A}">' + "".join(
        paragraph(value, "a") for value in values) + '</p:notes>'
    return part


def pdf_response(texts=("page one",), *, total=None, cut=False):
    return {"status": "ok", "pages_total": len(texts) if total is None else total,
            "pages": [{"page": i, "text": text, "truncated": cut and i == len(texts)}
                      for i, text in enumerate(texts, 1)],
            "truncated": cut or (total is not None and total > len(texts))}


def native(payload, *, code=0, stderr=b""):
    stdout = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
    return lambda command, **limits: (code, stdout, stderr)


class ParserTests(unittest.TestCase):
    def assertCode(self, code, data, filename, **kwargs):
        with self.assertRaises(parser.FileContentUnavailable) as raised:
            parser.parse_document_segments(data, filename, **kwargs)
        self.assertEqual(str(raised.exception), code)

    def assertContract(self, result):
        self.assertEqual(set(result), {"processing_version", "parser", "text", "text_sha256", "truncated", "scope", "segments"})
        self.assertEqual(result["processing_version"], "document-segments-v1")
        self.assertIs(type(result["truncated"]), bool)
        self.assertIs(type(result["scope"]), dict)
        self.assertIs(type(result["scope"]["body_complete"]), bool)
        if result["truncated"]:
            self.assertIs(result["scope"]["body_complete"], False)
        self.assertIs(type(result["segments"]), list)
        self.assertGreater(len(result["segments"]), 0)
        self.assertLessEqual(len(result["segments"]), 256)
        self.assertLessEqual(sum(len(x["text"].encode()) for x in result["segments"]), 16_000)
        self.assertLessEqual(len(result["text"].encode()), 16_000)
        self.assertEqual(result["text"], "\n".join(x["text"] for x in result["segments"]))
        self.assertEqual(result["text_sha256"], hashlib.sha256(result["text"].encode()).hexdigest())
        for segment in result["segments"]:
            self.assertEqual(set(segment), {"location", "text", "text_sha256"})
            self.assertIs(type(segment["location"]), dict)
            self.assertEqual(segment["text_sha256"], hashlib.sha256(segment["text"].encode()).hexdigest())

    def test_exact_api_and_existing_chat_api_unchanged(self):
        result = parser.parse_document_segments(b"a", "a.txt")
        self.assertContract(result)
        self.assertEqual(parser.MAX_FILE_BYTES, legacy.MAX_FILE_BYTES)
        self.assertEqual(parser.MAX_TEXT_BYTES, legacy.MAX_TEXT_BYTES)
        self.assertEqual(parser.MAX_ARCHIVE_BYTES, legacy.MAX_ARCHIVE_BYTES)
        self.assertEqual(parser.MAX_ARCHIVE_ENTRIES, legacy.MAX_ARCHIVE_ENTRIES)
        self.assertIs(parser.FileContentUnavailable, legacy.FileContentUnavailable)
        self.assertEqual(set(legacy.extract_document(b"a", "a.txt")), {"text", "parser", "text_sha256", "truncated", "scope"})

    def test_text_physical_lines_unicode_whitespace_units_and_duplicates(self):
        text = "  수량 42개\t3.5 kg  \r\n\r\n같음\r같음\n끝\u2028내용\n"
        result = parser.parse_document_segments(text.encode(), "memo.MD")
        self.assertEqual([x["text"] for x in result["segments"]], ["  수량 42개\t3.5 kg  ", "", "같음", "같음", "끝\u2028내용"])
        self.assertEqual([x["location"]["line_start"] for x in result["segments"]], [1, 2, 3, 4, 5])
        self.assertTrue(all(x["location"]["line_start"] == x["location"]["line_end"] for x in result["segments"]))
        self.assertFalse(result["truncated"])
        self.assertContract(result)

    def test_utf16_bom_preserves_original_line_numbers(self):
        for encoding in ("utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                result = parser.parse_document_segments("하나\n둘".encode(encoding), "a.txt")
                self.assertEqual(result["segments"][1]["location"]["line_start"], 2)
                self.assertEqual(result["text"], "하나\n둘")

    def test_html_and_code_are_source_lines_no_link_or_instruction_execution(self):
        value = '<script>do_not_execute()</script>\n<a href="https://invalid.example/secret"> 42 </a>'
        for name in ("a.html", "a.py", "a.json", "a.yaml", "a.sql"):
            with self.subTest(name=name):
                result = parser.parse_document_segments(value.encode(), name)
                self.assertEqual(result["text"], value)
                self.assertEqual(result["scope"]["kind"], "original-lines")

    def test_csv_multiline_quotes_blank_records_and_physical_positions(self):
        value = 'name,note\r\nA,"  line 1\r\nline ""2""  "\r\n\r\nA,same\r\nA,same\r\n'
        result = parser.parse_document_segments(value.encode(), "a.csv")
        self.assertEqual([(s["location"]["record_index"], s["location"]["line_start"], s["location"]["line_end"])
                          for s in result["segments"]], [(1, 1, 1), (2, 2, 3), (3, 4, 4), (4, 5, 5), (5, 6, 6)])
        row = next(csv.reader(io.StringIO(result["segments"][1]["text"], newline="")))
        self.assertEqual(row, ["A", '  line 1\r\nline "2"  '])
        self.assertEqual(result["segments"][3]["text"], result["segments"][4]["text"])
        self.assertContract(result)

    def test_tsv_multiline_cell_and_cr_physical_lines(self):
        result = parser.parse_document_segments('a\tb\r1\t" x\ry "\r'.encode(), "a.tsv")
        self.assertEqual(result["segments"][1]["location"], {"kind": "record", "record_index": 2, "line_start": 2, "line_end": 3})
        self.assertEqual(next(csv.reader(io.StringIO(result["segments"][1]["text"], newline=""), delimiter="\t")), ["1", " x\ry "])

    def test_csv_malformed_and_existing_field_budget_are_static_errors(self):
        for data in (b'a,"unterminated', b'a,"b"bad\n'):
            self.assertCode("document_csv_invalid", data, "a.csv")
        field_limit = csv.field_size_limit()
        if field_limit < parser.MAX_FILE_BYTES - 1:
            self.assertCode("document_csv_field_budget", b"x" * (field_limit + 1), "a.csv")
        self.assertEqual(csv.field_size_limit(), field_limit)

    def test_docx_parts_paragraphs_empty_positions_tabs_and_no_pages(self):
        parts = docx_parts(("", "  값 42개  ", "repeat", "repeat"))
        parts["word/header1.xml"] = f'<w:hdr xmlns:w="{W}">{paragraph("머리")}</w:hdr>'
        parts["word/footnotes.xml"] = f'<w:footnotes xmlns:w="{W}">{paragraph("주석")}</w:footnotes>'
        parts["word/document.xml"] = parts["word/document.xml"].replace('</w:body>', '<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p></w:body>')
        result = parser.parse_document_segments(archive(parts), "a.docx")
        self.assertEqual(result["segments"][1]["location"], {"kind": "paragraph", "part": "word/document.xml", "paragraph": 2})
        self.assertEqual(result["segments"][1]["text"], "  값 42개  ")
        self.assertEqual(result["segments"][4]["text"], "A\tB\nC")
        self.assertEqual(result["segments"][-1]["location"], {"kind": "paragraph", "part": "word/header1.xml", "paragraph": 1})
        self.assertFalse(result["scope"]["pages_known"])
        self.assertTrue(all("page" not in x["location"] for x in result["segments"]))
        self.assertContract(result)

    def test_docx_nested_textbox_paragraph_not_duplicated_in_parent(self):
        parts = docx_parts(("outer",))
        parts["word/document.xml"] = parts["word/document.xml"].replace('</w:r></w:p>', '</w:r><w:txbxContent>' + paragraph("inside") + '</w:txbxContent></w:p>')
        result = parser.parse_document_segments(archive(parts), "a.docx")
        self.assertEqual([s["text"] for s in result["segments"]], ["outer", "inside"])
        self.assertEqual([s["location"]["paragraph"] for s in result["segments"]], [1, 2])

    def test_xlsx_sheet_identity_actual_coordinates_and_duplicates(self):
        rows = '<x:row r="9"><x:c r="D9" t="s"><x:v>0</x:v></x:c><x:c r="F9" t="inlineStr"><x:is><x:t xml:space="preserve"> 3.5 kg </x:t></x:is></x:c><x:c r="H9" t="s"><x:v>0</x:v></x:c></x:row>'
        result = parser.parse_document_segments(archive(xlsx_parts([("첫 시트", "19", rows), ("둘", "3", '<x:row r="1048576"><x:c r="XFD1048576"><x:v>99</x:v></x:c></x:row>')])), "a.xlsx")
        self.assertEqual([s["location"]["cell"] for s in result["segments"]], ["D9", "F9", "H9", "XFD1048576"])
        first = result["segments"][0]["location"]
        self.assertEqual((first["sheet_index"], first["sheet_id"], first["sheet_name"], first["part"]), (1, "19", "첫 시트", "xl/worksheets/data1.xml"))
        self.assertEqual(result["segments"][0]["text"], result["segments"][2]["text"])
        self.assertEqual(result["segments"][1]["text"], " 3.5 kg ")
        self.assertEqual(result["segments"][3]["location"]["sheet_index"], 2)
        self.assertContract(result)

    def test_xlsx_formula_cache_missing_cache_and_shared_formula_no_evaluation(self):
        rows = '<x:row r="1"><x:c r="A1"><x:f>21*2</x:f><x:v>42</x:v></x:c><x:c r="B1"><x:f>HYPERLINK("https://invalid.example","click")</x:f></x:c><x:c r="C1"><x:f t="shared" si="0"/><x:v>84</x:v></x:c></x:row>'
        result = parser.parse_document_segments(archive(xlsx_parts([("formula", "1", rows)])), "a.xlsx")
        self.assertEqual(json.loads(result["segments"][0]["text"]), {"formula": "21*2", "cached_value": "42"})
        self.assertIsNone(json.loads(result["segments"][1]["text"])["cached_value"])
        self.assertFalse(result["segments"][1]["location"]["cached_value_present"])
        self.assertEqual(result["segments"][2]["location"]["formula_kind"], "shared")
        self.assertEqual(json.loads(result["segments"][2]["text"]), {"formula": "", "cached_value": "84"})
        self.assertFalse(result["scope"]["formulas_evaluated"])
        self.assertTrue(result["scope"]["cached_values_may_be_stale"])

    def test_xlsx_raw_numeric_boolean_date_and_error_values_preserved(self):
        rows = '<x:row>' + ''.join(f'<x:c r="{cell}" t="{kind}"><x:v>{value}</x:v></x:c>' for cell, kind, value in (
            ("A4", "n", "0042.00"), ("B4", "b", "1"), ("C4", "d", "2026-10-28T00:00:00"), ("D4", "e", "#N/A"))) + '</x:row>'
        result = parser.parse_document_segments(archive(xlsx_parts([("raw", "1", rows)])), "a.xlsx")
        self.assertEqual([s["text"] for s in result["segments"]], ["0042.00", "1", "2026-10-28T00:00:00", "#N/A"])
        self.assertFalse(result["scope"]["number_formats_applied"])

    def test_xlsx_malicious_string_indices(self):
        for value in ("-1", "1", "9999999999999999999999999", "²", "１", "1.0", ""):
            rows = f'<x:row><x:c r="A1" t="s"><x:v>{value}</x:v></x:c></x:row>'
            with self.subTest(value=value):
                self.assertCode("document_shared_string_invalid", archive(xlsx_parts([("a", "1", rows)])), "a.xlsx")

    def test_xlsx_malicious_coordinates_and_row_mismatch(self):
        for coordinate in ("", "A0", "A01", "A1048577", "XFE1", "AAAA1", "a1", "A-1", "A9999999999999", "$A$1", "A１"):
            rows = f'<x:row><x:c r="{coordinate}"><x:v>1</x:v></x:c></x:row>'
            with self.subTest(coordinate=coordinate):
                self.assertCode("document_cell_coordinate_invalid", archive(xlsx_parts([("a", "1", rows)])), "a.xlsx")
        rows = '<x:row r="2"><x:c r="A1"><x:v>1</x:v></x:c></x:row>'
        self.assertCode("document_cell_coordinate_invalid", archive(xlsx_parts([("a", "1", rows)])), "a.xlsx")

    def test_xlsx_malformed_cell_shape_formula_index_and_style(self):
        cases = [('<x:c r="A1"><x:v>1</x:v><x:v>2</x:v></x:c>', "document_cell_structure_invalid"),
                 ('<x:c r="A1"><x:f si="-1">x</x:f></x:c>', "document_formula_invalid"),
                 ('<x:c r="A1"><x:f t="evil">x</x:f></x:c>', "document_formula_invalid"),
                 ('<x:c r="A1" s="9999999999999999"><x:v>1</x:v></x:c>', "document_cell_style_invalid"),
                 ('<x:c r="A1" t="evil"><x:v>1</x:v></x:c>', "document_cell_type_invalid"),
                 ('<x:c r="A1"><x:v>1</x:v></x:c>' * 2, "document_cell_coordinate_invalid")]
        for cells, code in cases:
            with self.subTest(code=code):
                self.assertCode(code, archive(xlsx_parts([("a", "1", '<x:row>' + cells + '</x:row>')])), "a.xlsx")

    def test_pptx_actual_presentation_order_and_paragraph_positions(self):
        result = parser.parse_document_segments(archive(pptx_parts((("last",), ("", "  먼저 42개  ", "same", "same")))), "a.pptx")
        self.assertEqual(result["segments"][1]["location"], {"kind": "paragraph", "part": "ppt/slides/slide2.xml", "slide": 1, "slide_id": "256", "paragraph": 2})
        self.assertEqual(result["segments"][1]["text"], "  먼저 42개  ")
        self.assertEqual(result["segments"][-1]["location"]["slide"], 2)
        self.assertEqual(result["segments"][-1]["text"], "last")
        self.assertFalse(result["scope"]["notes_read"])
        self.assertTrue(all("page" not in s["location"] for s in result["segments"]))
        self.assertContract(result)

    def test_no_text_office_and_wrong_structure(self):
        for name, parts in (("a.docx", docx_parts(("  ", ""))), ("a.xlsx", xlsx_parts([("a", "1", "")])),
                            ("a.pptx", pptx_parts((("",),)))):
            with self.subTest(name=name):
                self.assertCode("document_has_no_text", archive(parts), name)
        self.assertCode("document_format_mismatch", archive({"x.xml": "<x/>"}), "a.docx")
        self.assertCode("document_docx_structure_invalid", archive({"word/document.xml": "<wrong/>"}), "a.docx")

    def test_input_byte_budget_encoding_binary_unknown_and_filename(self):
        for data in (b"", b"a" * (parser.MAX_FILE_BYTES + 1), bytearray(b"x"), None):
            with self.subTest(kind=type(data).__name__):
                self.assertCode("document_byte_budget", data, "a.txt")
        self.assertCode("document_encoding_unavailable", b"\xff", "a.txt")
        self.assertCode("document_contains_binary_text", b"a\x00b", "a.txt")
        self.assertCode("document_format_unsupported", b"a", "a.exe")
        for name in (None, "", "a\x00.txt", "a" * 4097):
            self.assertCode("document_filename_invalid", b"a", name)
        self.assertCode("document_has_no_text", b" \t\r\n", "a.txt")

    def test_exact_maximum_input_is_accepted_and_bounded(self):
        result = parser.parse_document_segments(b"x" * parser.MAX_FILE_BYTES, "a.txt")
        self.assertEqual(len(result["text"]), 16_000)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scope"]["partial_segment"], 1)
        self.assertContract(result)

    def test_utf8_text_boundary_and_join_separator_budget(self):
        for value in ("한" * 6000, "한\n" * 5000, "a" * 15999 + "\n한"):
            with self.subTest(length=len(value)):
                result = parser.parse_document_segments(value.encode(), "a.txt")
                self.assertTrue(result["truncated"])
                self.assertContract(result)
        exact = parser.parse_document_segments(b"a" * 8000 + b"\n" + b"b" * 7999, "a.txt")
        self.assertEqual(len(exact["text"]), 16_000)
        self.assertFalse(exact["truncated"])

    def test_segment_limit_exact_and_omitted_disclosed(self):
        exact = parser.parse_document_segments("\n".join(["x"] * 256).encode(), "a.md")
        self.assertFalse(exact["truncated"])
        result = parser.parse_document_segments("\n".join(["x"] * 259).encode(), "a.md")
        self.assertEqual(len(result["segments"]), 256)
        self.assertEqual(result["scope"]["segments_omitted"], 3)
        self.assertEqual(result["scope"]["segment_candidates_seen"], 259)
        self.assertTrue(result["truncated"])
        self.assertContract(result)

    def test_csv_and_office_segment_limits(self):
        values = [(b"x\n" * 260, "a.csv"), (archive(docx_parts(["x"] * 260)), "a.docx"),
                  (archive(pptx_parts((["x"] * 260,))), "a.pptx")]
        rows = '<x:row>' + ''.join(f'<x:c r="A{i}"><x:v>1</x:v></x:c>' for i in range(1, 261)) + '</x:row>'
        values.append((archive(xlsx_parts([("a", "1", rows)])), "a.xlsx"))
        for data, name in values:
            with self.subTest(name=name):
                result = parser.parse_document_segments(data, name)
                self.assertEqual(len(result["segments"]), 256)
                self.assertEqual(result["scope"]["segments_omitted"], 4)
                self.assertTrue(result["truncated"])
                self.assertContract(result)

    def test_shared_string_amplification_and_large_formula_are_bounded(self):
        rows = '<x:row>' + ''.join(f'<x:c r="A{i}" t="s"><x:v>0</x:v></x:c>' for i in range(1, 15001)) + '</x:row>'
        result = parser.parse_document_segments(archive(xlsx_parts([("a", "1", rows)], ["한" * 6000])), "a.xlsx")
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["scope"]["segments_omitted"], 14999)
        self.assertContract(result)
        rows = '<x:row><x:c r="A1"><x:f>' + "x" * 40_000 + '</x:f><x:v>42</x:v></x:c></x:row>'
        result = parser.parse_document_segments(archive(xlsx_parts([("a", "1", rows)])), "a.xlsx")
        self.assertTrue(result["truncated"])
        self.assertContract(result)

    def test_sheet_and_slide_bounds_explicit_and_omitted_parts_validated(self):
        cells = '<x:row><x:c r="A1"><x:v>1</x:v></x:c></x:row>'
        parts = xlsx_parts([(f"s{i}", str(i), cells) for i in range(1, 52)])
        result = parser.parse_document_segments(archive(parts), "a.xlsx")
        self.assertEqual((result["scope"]["sheets_read"], result["scope"]["sheets_total"], result["scope"]["sheets_omitted"]), (50, 51, 1))
        self.assertEqual(len(result["segments"]), 50)
        self.assertEqual(result["scope"]["segments_omitted"], 1)
        self.assertTrue(result["truncated"])
        parts["xl/worksheets/data51.xml"] = parts["xl/worksheets/data51.xml"].replace('r="A1"', 'r="XFE1"')
        self.assertCode("document_cell_coordinate_invalid", archive(parts), "a.xlsx")
        result = parser.parse_document_segments(archive(pptx_parts([("x",)] * 51)), "a.pptx")
        self.assertEqual((result["scope"]["slides_read"], result["scope"]["slides_total"], result["scope"]["slides_omitted"]), (50, 51, 1))
        self.assertEqual(result["scope"]["segments_omitted"], 1)
        self.assertTrue(result["truncated"])
        self.assertContract(result)

    def test_archive_entry_count_duplicate_expansion_and_encryption_bounds(self):
        self.assertCode("document_archive_budget_or_shape", archive({f"a{i}.xml": "<x/>" for i in range(257)}), "a.docx")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate = archive([("word/document.xml", "<x/>"), ("word/document.xml", "<x/>")])
        self.assertCode("document_archive_budget_or_shape", duplicate, "a.docx")
        self.assertCode("document_archive_budget_or_shape", archive({"big": b"x" * (parser.MAX_ARCHIVE_BYTES + 1)}), "a.docx")
        encrypted = bytearray(archive(docx_parts()))
        local = encrypted.index(b"PK\x03\x04")
        central = encrypted.index(b"PK\x01\x02")
        struct.pack_into("<H", encrypted, local + 6, struct.unpack_from("<H", encrypted, local + 6)[0] | 1)
        struct.pack_into("<H", encrypted, central + 8, struct.unpack_from("<H", encrypted, central + 8)[0] | 1)
        self.assertCode("document_archive_budget_or_shape", bytes(encrypted), "a.docx")

    def test_archive_corrupt_zip_deflate_paths_and_symlinks(self):
        self.assertCode("document_archive_invalid", b"not zip", "a.docx")
        corrupt = bytearray(archive(docx_parts()))
        name_size, extra_size = struct.unpack_from("<HH", corrupt, 26)
        offset = 30 + name_size + extra_size
        corrupt[offset] = (corrupt[offset] & ~6) | 6
        self.assertCode("document_archive_invalid", bytes(corrupt), "a.docx")
        for member in ("../secret.xml", "/secret.xml", "word/../secret.xml", "C:/secret.xml", "word\\secret.xml", "word/%2e%2e.xml"):
            parts = docx_parts(); parts[member] = "<x/>"
            with self.subTest(member=member):
                self.assertCode("document_archive_unsafe", archive(parts), "a.docx")
        info = zipfile.ZipInfo("word/link")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("word/document.xml", docx_parts()["word/document.xml"])
            z.writestr(info, "../../private")
        self.assertCode("document_archive_unsafe", output.getvalue(), "a.docx")

    def test_xml_dtd_entities_utf16_and_malformed_are_rejected(self):
        entity = '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///never/read">]><x>&secret;</x>'
        for data in (entity.encode(), entity.encode("utf-16"), entity.encode("utf-16-le")):
            with self.subTest(prefix=data[:8]):
                with self.assertRaises(parser.FileContentUnavailable):
                    parser.parse_document_segments(data, "a.xml")
        self.assertCode("document_xml_entities_unsupported", entity.encode(), "a.xml")
        self.assertCode("document_xml_invalid", b"<x>", "a.xml")
        parts = docx_parts(); parts["ignored.xml"] = entity
        self.assertCode("document_xml_entities_unsupported", archive(parts), "a.docx")
        parts["ignored.xml"] = "<broken>"
        self.assertCode("document_xml_invalid", archive(parts), "a.docx")

    def test_xml_node_budget(self):
        parts = docx_parts(); parts["many.xml"] = '<x>' + '<e/>' * parser.MAX_XML_NODES + '</x>'
        self.assertCode("document_xml_node_budget", archive(parts), "a.docx")

    def test_unsafe_relations_all_formats_and_ignored_parts(self):
        for target, mode in (("https://invalid.example/private", "External"), ("../../outside", "Internal"),
                             ("file:///private", "Internal"), ("worksheets/%2e%2e.xml", "Internal"),
                             ("//host/share", "Internal"), ("worksheets/missing.xml", "Internal")):
            parts = xlsx_parts()
            parts["xl/_rels/workbook.xml.rels"] = f'<Relationships><Relationship Id="r1" Target="{target}" TargetMode="{mode}"/></Relationships>'
            with self.subTest(target=target):
                self.assertCode("document_archive_relation_unsafe", archive(parts), "a.xlsx")
        parts = docx_parts()
        parts["word/_rels/unused.xml.rels"] = '<Relationships><Relationship Id="x" Target="https://invalid.example" TargetMode="External"/></Relationships>'
        self.assertCode("document_archive_relation_unsafe", archive(parts), "a.docx")
        parts = pptx_parts()
        parts["ppt/_rels/presentation.xml.rels"] = parts["ppt/_rels/presentation.xml.rels"].replace('Target="slides/slide1.xml"', 'Target="../outside.xml"')
        self.assertCode("document_archive_relation_unsafe", archive(parts), "a.pptx")

    def test_safe_archive_relative_and_absolute_relations(self):
        parts = xlsx_parts()
        parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace('worksheets/data1.xml', '/xl/worksheets/data1.xml')
        parts["xl/_rels/extra.xml.rels"] = '<Relationships><Relationship Id="x" Target="../xl/sharedStrings.xml"/></Relationships>'
        result = parser.parse_document_segments(archive(parts), "a.xlsx")
        self.assertEqual(result["text"], "올든 42개")

    def test_missing_duplicate_sheet_slide_relation_or_identity(self):
        for suffix, parts, root_part in ((".xlsx", xlsx_parts(), "xl/workbook.xml"),
                                         (".pptx", pptx_parts(), "ppt/presentation.xml")):
            parts[root_part] = parts[root_part].replace('r:id="r1"', 'r:id="missing"')
            with self.subTest(suffix=suffix):
                self.assertCode("document_sheet_relation_invalid" if suffix == ".xlsx" else "document_slide_relation_invalid", archive(parts), "a" + suffix)
        self.assertCode("document_sheet_relation_invalid", archive(xlsx_parts([("a", "1", ""), ("b", "01", "")])), "a.xlsx")
        self.assertCode("document_sheet_identity_invalid", archive(xlsx_parts([("a", "1", ""), ("a", "2", "")])), "a.xlsx")

    def test_pdf_native_required_magic_and_fixed_limits_private_copy_cleanup(self):
        self.assertCode("document_native_parser_unavailable", PDF, "a.pdf")
        self.assertCode("document_format_mismatch", b"text", "a.pdf", run_process=native(pdf_response()))
        seen = []

        def run(command, **limits):
            self.assertEqual(command[:3], ["/usr/bin/osascript", "-l", "JavaScript"])
            self.assertIn("PDFKit", command[4])
            self.assertEqual(limits, {"timeout": 8.0, "stdout_cap": 256_000, "stderr_cap": 4096})
            path = Path(command[-1]); seen.append(path)
            self.assertEqual(path.read_bytes(), PDF)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            if os.environ.get("ALDEN_TEST_TMPDIR"):
                self.assertEqual(path.parent.parent.resolve(), Path(os.environ["ALDEN_TEST_TMPDIR"]).resolve())
            return native(pdf_response(("real page",)))(command, **limits)

        result = parser.parse_document_segments(PDF, "a.pdf", run_process=run)
        self.assertEqual(result["segments"][0]["location"], {"kind": "page", "page": 1})
        self.assertFalse(seen[0].exists())
        self.assertContract(result)

    def test_pdf_actual_pages_blank_pages_and_literal_page_labels_not_inferred(self):
        result = parser.parse_document_segments(PDF, "a.pdf", run_process=native(pdf_response(("[Page 999]\n first 42개 ", "", "third"))))
        self.assertEqual([s["location"]["page"] for s in result["segments"]], [1, 2, 3])
        self.assertEqual(result["segments"][0]["text"], "[Page 999]\n first 42개 ")
        self.assertEqual(result["segments"][1]["text"], "")
        self.assertFalse(result["scope"]["ocr_performed"])
        self.assertFalse(result["truncated"])
        self.assertContract(result)

    def test_pdf_page_and_native_text_truncation_disclosed(self):
        result = parser.parse_document_segments(PDF, "a.pdf", run_process=native(pdf_response(["x"] * 50, total=75)))
        self.assertEqual((result["scope"]["pages_read"], result["scope"]["pages_total"], result["scope"]["pages_omitted"]), (50, 75, 25))
        self.assertTrue(result["truncated"])
        result = parser.parse_document_segments(PDF, "a.pdf", run_process=native(pdf_response(["한" * 6000], total=2, cut=True)))
        self.assertTrue(result["scope"]["native_text_truncated"])
        self.assertTrue(result["truncated"])
        self.assertContract(result)

    def test_pdf_unavailable_locked_empty_scanned_and_malformed(self):
        for status, code in (("locked", "document_pdf_locked"), ("malformed", "document_pdf_invalid"),
                             ("empty", "document_pdf_empty"), ("unavailable", "document_pdf_unavailable")):
            with self.subTest(status=status):
                self.assertCode(code, PDF, "a.pdf", run_process=native({"status": status}))
        self.assertCode("document_pdf_has_no_text", PDF, "a.pdf", run_process=native(pdf_response(["", " \n\t"])))
        self.assertCode("document_pdf_response_invalid", PDF, "a.pdf", run_process=native(b"not JSON /private/secret"))

    def test_pdf_response_fields_and_real_page_sequence_validated(self):
        mutations = [lambda x: x.update(pages_total=True), lambda x: x.update(pages_total=0),
                     lambda x: x.update(truncated="yes"), lambda x: x.update(truncated=True),
                     lambda x: x.update(pages=[]), lambda x: x["pages"][0].update(page=2),
                     lambda x: x["pages"][0].update(page=True), lambda x: x["pages"][0].update(text=42),
                     lambda x: x["pages"][0].update(truncated=1), lambda x: x.update(pages_total=2)]
        for mutate in mutations:
            payload = pdf_response(); mutate(payload)
            with self.subTest(payload=payload):
                self.assertCode("document_pdf_response_invalid", PDF, "a.pdf", run_process=native(payload))
        self.assertCode("document_pdf_response_invalid", PDF, "a.pdf", run_process=native(pdf_response(["x"] * 51)))
        self.assertCode("document_encoding_unavailable", PDF, "a.pdf", run_process=native(b'{"status":"ok","pages_total":1,"pages":[{"page":1,"text":"\\ud800","truncated":false}],"truncated":false}'))

    def test_native_failures_caps_response_and_exception_messages_sanitized(self):
        for response, code in (((1, b"", b"/private/secret"), "document_native_parser_failed"),
                               ((0, b"x" * 256001, b""), "document_native_output_budget"),
                               ((0, b"", b"x" * 4097), "document_native_output_budget"),
                               ((0, "string", b""), "document_native_response_invalid"),
                               ((False, b"", b""), "document_native_response_invalid"),
                               ((0, b""), "document_native_response_invalid")):
            with self.subTest(code=code):
                self.assertCode(code, PDF, "a.pdf", run_process=lambda *a, **k: response)
        for error, code in ((OSError("/private/secret"), "document_native_parser_failed"),
                            (parser.FileContentUnavailable("/private/secret"), "document_native_parser_failed"),
                            (subprocess.TimeoutExpired("private", 8), "document_native_parser_timeout"),
                            (TimeoutError("private"), "document_native_parser_timeout")):
            def run(*args, **kwargs):
                raise error
            self.assertCode(code, PDF, "a.pdf", run_process=run)

    def test_text_budget_reports_structures_without_emitted_segments(self):
        result = parser.parse_document_segments(PDF, "a.pdf", run_process=native(pdf_response(["x" * 16_001, "later"])))
        self.assertEqual(result["scope"]["pages_read"], 2)
        self.assertEqual(result["scope"]["pages_omitted"], 0)
        self.assertEqual(result["scope"]["pages_without_segments"], 1)
        self.assertEqual(result["scope"]["segments_omitted"], 1)
        self.assertTrue(result["truncated"])
        cells = '<x:row><x:c r="A1" t="inlineStr"><x:is><x:t>' + "x" * 16_001 + '</x:t></x:is></x:c></x:row>'
        result = parser.parse_document_segments(archive(xlsx_parts([("one", "1", cells), ("two", "2", cells)])), "a.xlsx")
        self.assertEqual(result["scope"]["sheets_without_segments"], 1)
        result = parser.parse_document_segments(archive(pptx_parts([("x" * 16_001,), ("later",)], order=[1, 2])), "a.pptx")
        self.assertEqual(result["scope"]["slides_without_segments"], 1)
        self.assertContract(result)

    def test_rtf_doc_only_converted_line_scope_and_no_original_pages(self):
        for data, name in ((b"{\\rtf1 synthetic}", "a.rtf"), (bytes.fromhex("d0cf11e0a1b11ae1") + b"synthetic", "a.doc")):
            commands = []

            def run(command, **limits):
                commands.append(command)
                return 0, "  값 42개  \n둘".encode(), b""

            result = parser.parse_document_segments(data, name, run_process=run)
            self.assertEqual(commands[0][:4], ["/usr/bin/textutil", "-convert", "txt", "-stdout"])
            self.assertEqual(result["scope"]["kind"], "converted-lines")
            self.assertFalse(result["scope"]["original_positions"])
            self.assertEqual(result["segments"][0]["location"]["kind"], "converted_line")
            self.assertEqual(result["segments"][0]["text"], "  값 42개  ")
            self.assertTrue(all("page" not in s["location"] for s in result["segments"]))
            self.assertContract(result)

    def test_cancelled_before_parsing_and_native_dispatch(self):
        called = []
        self.assertCode("document_cancelled", PDF, "a.pdf", cancelled=lambda: True,
                        run_process=lambda *a, **k: called.append(1))
        self.assertEqual(called, [])

    def test_cancelled_during_text_csv_office_and_xml_work(self):
        cases = [(b"x\n" * 100, "a.txt"), (b"a,b\n" * 100, "a.csv"),
                 (archive(docx_parts(["x"] * 100)), "a.docx"),
                 (archive(xlsx_parts()), "a.xlsx"), (archive(pptx_parts()), "a.pptx"),
                 (b"<x>" + b"<a/>" * 10_000 + b"</x>", "a.xml")]
        for data, name in cases:
            calls = [0]

            def cancelled():
                calls[0] += 1
                return calls[0] >= 8

            with self.subTest(name=name):
                self.assertCode("document_cancelled", data, name, cancelled=cancelled)

    def test_cancellation_after_native_returns_cleans_private_copy(self):
        state, seen = [False], []

        def run(command, **limits):
            seen.append(Path(command[-1]))
            state[0] = True
            return native(pdf_response())(command, **limits)

        self.assertCode("document_cancelled", PDF, "a.pdf", run_process=run, cancelled=lambda: state[0])
        self.assertFalse(seen[0].parent.exists())

    def test_fresh_ownership_all_formats_and_locations(self):
        fixtures = [(b"same\nsame", "a.txt", {}), (b"x,y\nx,y", "a.csv", {}),
                    (archive(docx_parts(("x", "x"))), "a.docx", {}),
                    (archive(xlsx_parts()), "a.xlsx", {}), (archive(pptx_parts()), "a.pptx", {}),
                    (PDF, "a.pdf", {"run_process": native(pdf_response(("x", "x")))})]
        for data, name, kwargs in fixtures:
            with self.subTest(name=name):
                first = parser.parse_document_segments(data, name, **kwargs)
                expected = copy.deepcopy(first)
                second = parser.parse_document_segments(data, name, **kwargs)
                self.assertIsNot(first, second)
                self.assertIsNot(first["segments"], second["segments"])
                self.assertIsNot(first["scope"], second["scope"])
                for a, b in zip(first["segments"], second["segments"]):
                    self.assertIsNot(a, b)
                    self.assertIsNot(a["location"], b["location"])
                first["scope"].clear()
                first["segments"][0]["location"].clear()
                first["segments"][0]["text"] = "mutated"
                first["segments"].clear()
                self.assertEqual(second, expected)
                self.assertContract(second)


    def test_pptx_finding3_linked_notes_counterexample(self):
        # independent-review/probes.py finding3: the validator accepts an untyped
        # link too; those linked notes must not silently disappear.
        parts = pptx_parts((("Slide body",),))
        note = link_notes(parts, 1, ("Notes-only instruction 7429",))
        rel = "ppt/slides/_rels/slide1.xml.rels"
        parts[rel] = parts[rel].replace(f' Type="{R}/notesSlide"', '')
        result = parser.parse_document_segments(archive(parts), "a.pptx")
        self.assertEqual(result["text"], "Slide body\nNotes-only instruction 7429")
        self.assertEqual(result["segments"][1]["location"], {
            "kind": "paragraph", "part": note, "slide": 1, "slide_id": "256", "paragraph": 1})
        self.assertIs(result["scope"]["notes_read"], True)
        self.assertEqual(result["scope"]["notes_parts_read"], [note])
        self.assertEqual(result["scope"]["notes_parts_omitted"], [])
        self.assertFalse(result["truncated"])
        self.assertIs(result["scope"]["body_complete"], False)
        self.assertIn("other text not certified", result["scope"]["body_coverage"])
        self.assertContract(result)

    def test_pptx_notes_follow_slide_links_and_source_paragraphs_not_zip_order(self):
        parts = pptx_parts((("last slide",), ("first slide",)), order=[2, 1])
        last = link_notes(parts, 1, ("last notes",), name="alpha.xml")
        first = link_notes(parts, 2, ("", "first notes", "same", "same"), name="zulu.xml")
        orphan = "ppt/notesSlides/orphan.xml"
        parts[orphan] = f'<p:notes xmlns:p="{P}" xmlns:a="{A}">{paragraph("unlinked secret", "a")}</p:notes>'
        rel = "ppt/slides/_rels/slide2.xml.rels"
        parts[rel] = parts[rel].replace('../notesSlides/zulu.xml', '/ppt/notesSlides/zulu.xml')
        result = parser.parse_document_segments(archive(list(reversed(list(parts.items())))), "a.pptx")
        self.assertEqual([s["text"] for s in result["segments"]], [
            "first slide", "", "first notes", "same", "same", "last slide", "last notes"])
        self.assertEqual(result["scope"]["notes_parts_read"], [first, last])
        self.assertEqual(result["segments"][2]["location"], {
            "kind": "paragraph", "part": first, "slide": 1, "slide_id": "256", "paragraph": 2})
        self.assertEqual(result["segments"][-1]["location"]["slide"], 2)
        self.assertEqual(result["scope"]["notes_parts_omitted"], [orphan])
        self.assertIn(orphan, result["scope"]["parts_without_text_extraction"])
        self.assertNotIn("unlinked secret", result["text"])
        self.assertIs(result["scope"]["body_complete"], False)
        self.assertContract(result)

    def test_pptx_notes_share_text_and_segment_limits(self):
        for slide, notes, partial, omitted, no_note_segment in (
                ("x" * 15_999, ("한",), None, 1, True),
                ("body", ("한" * 6000, "later"), 2, 1, False),
                ("body", ("note",) * 256, None, 1, False)):
            with self.subTest(partial=partial, length=len(slide)):
                parts = pptx_parts(((slide,),))
                note = link_notes(parts, 1, notes)
                result = parser.parse_document_segments(archive(parts), "a.pptx")
                self.assertTrue(result["truncated"])
                self.assertIs(result["scope"]["body_complete"], False)
                self.assertEqual(result["scope"]["partial_segment"], partial)
                self.assertEqual(result["scope"]["segments_omitted"], omitted)
                self.assertEqual(result["scope"]["notes_parts_read"], [note])
                self.assertEqual(result["scope"]["notes_parts_without_segments"], [note] if no_note_segment else [])
                self.assertContract(result)

    def test_pptx_notes_beyond_slide_limit_are_omitted_and_still_validated(self):
        parts = pptx_parts([("body",)] * 51, order=list(range(1, 52)))
        note = link_notes(parts, 51, ("omitted notes",))
        result = parser.parse_document_segments(archive(parts), "a.pptx")
        self.assertEqual(len(result["segments"]), 50)
        self.assertEqual(result["scope"]["slides_omitted"], 1)
        self.assertEqual(result["scope"]["segments_omitted"], 2)
        self.assertEqual(result["scope"]["notes_parts_read"], [])
        self.assertEqual(result["scope"]["notes_parts_omitted"], [note])
        self.assertTrue(result["truncated"])
        self.assertIs(result["scope"]["body_complete"], False)
        self.assertContract(result)
        parts[note] = '<wrong/>'
        self.assertCode("document_notes_structure_invalid", archive(parts), "a.pptx")
        parts[note] = '<!DOCTYPE x [<!ENTITY a "text">]><notes>&a;</notes>'
        self.assertCode("document_xml_entities_unsupported", archive(parts), "a.pptx")

    def test_pptx_notes_relationship_safety_and_ambiguity(self):
        parts = pptx_parts((("body",),))
        link_notes(parts, 1, ("notes",))
        rel = "ppt/slides/_rels/slide1.xml.rels"
        original = parts[rel]
        for target in ("https://invalid.example/notes.xml", "../../../outside.xml", "../notesSlides/missing.xml"):
            with self.subTest(target=target):
                parts[rel] = original.replace('../notesSlides/notesSlide1.xml', target)
                self.assertCode("document_archive_relation_unsafe", archive(parts), "a.pptx")
        parts[rel] = original.replace('</Relationships>', '<Relationship Id="duplicate" Target="../notesSlides/notesSlide1.xml"/></Relationships>')
        self.assertCode("document_notes_relation_invalid", archive(parts), "a.pptx")
        parts = pptx_parts()
        link_notes(parts, 1, ("notes",))
        parts["ppt/slides/_rels/slide2.xml.rels"] = parts["ppt/slides/_rels/slide1.xml.rels"]
        self.assertCode("document_notes_relation_invalid", archive(parts), "a.pptx")

    def test_pptx_cancellation_during_linked_notes_traversal(self):
        parts = pptx_parts((("body",),))
        link_notes(parts, 1, ("note",) * 100)
        original = parser._paragraph_text
        seen = [0]

        def paragraph_text(root, cancelled):
            seen[0] += 1
            return original(root, cancelled)

        from unittest.mock import patch
        with patch.object(parser, "_paragraph_text", side_effect=paragraph_text):
            self.assertCode("document_cancelled", archive(parts), "a.pptx", cancelled=lambda: seen[0] >= 3)
        self.assertEqual(seen[0], 3)


    def test_body_complete_true_for_small_source_text_and_records_only(self):
        for data, name in ((b"one\r\ntwo", "a.txt"), (b'a,"one\ntwo"\n', "a.csv"),
                           (b"a\tb\n", "a.tsv"), (b"<x>body</x>", "a.xml")):
            with self.subTest(name=name):
                result = parser.parse_document_segments(data, name)
                self.assertFalse(result["truncated"])
                self.assertIs(result["scope"]["body_complete"], True)
                self.assertContract(result)

    def test_body_complete_false_for_partial_text_and_omitted_records(self):
        for data, name in ((b"x" * 16_001, "a.txt"), (b"x\n" * 257, "a.txt"),
                           (b"x\n" * 257, "a.csv"), (b"x" * 16_001, "a.tsv")):
            with self.subTest(name=name, length=len(data)):
                result = parser.parse_document_segments(data, name)
                self.assertTrue(result["truncated"])
                self.assertIs(result["scope"]["body_complete"], False)
                self.assertContract(result)

    def test_office_unparsed_text_parts_never_imply_original_body_complete(self):
        fixtures = [
            ("a.docx", docx_parts(), "word/comments.xml", f'<w:comments xmlns:w="{W}">{paragraph("comment-only 7429")}</w:comments>'),
            ("a.xlsx", xlsx_parts(), "xl/comments1.xml", f'<x:comments xmlns:x="{X}"><x:comment><x:text><x:t>comment-only 7429</x:t></x:text></x:comment></x:comments>'),
            ("a.pptx", pptx_parts(), "ppt/charts/chart1.xml", f'<c:chartSpace xmlns:c="chart" xmlns:a="{A}">{paragraph("comment-only 7429", "a")}</c:chartSpace>'),
        ]
        for name, parts, unparsed, xml in fixtures:
            parts[unparsed] = xml
            with self.subTest(name=name):
                result = parser.parse_document_segments(archive(parts), name)
                self.assertFalse(result["truncated"])
                self.assertIs(result["scope"]["body_complete"], False)
                self.assertIn(unparsed, result["scope"]["parts_without_text_extraction"])
                self.assertIn("not certified", result["scope"]["body_coverage"])
                self.assertNotIn("comment-only 7429", result["text"])
                self.assertContract(result)

    def test_native_text_coverage_is_conservative_even_without_truncation(self):
        for data, name, output in ((PDF, "a.pdf", pdf_response()),
                                   (b"{\\rtf1 synthetic}", "a.rtf", b"converted body"),
                                   (bytes.fromhex("d0cf11e0a1b11ae1") + b"synthetic", "a.doc", b"converted body")):
            with self.subTest(name=name):
                result = parser.parse_document_segments(data, name, run_process=native(output))
                self.assertFalse(result["truncated"])
                self.assertIs(result["scope"]["body_complete"], False)
                self.assertIn("not proven", result["scope"]["body_coverage"])
                self.assertContract(result)


if __name__ == "__main__":
    unittest.main()
