"""Small, dependency-free XLSX report. All user and source text is stored as strings."""

import re
from math import ceil
from unicodedata import east_asian_width
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _text(value):
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)))


def column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def excel_chunks(value):
    # Leave room for Unicode surrogate pairs and split at a visible boundary when possible.
    chunks = []
    while len(value) > 16000:
        cut = max(value.rfind(mark, 0, 16000) for mark in ("、", "\n", " ")) + 1 or 16000
        chunks.append(value[:cut])
        value = value[cut:]
    return [*chunks, value]


def wrapped_lines(value, width):
    # Column widths are in character units; a CJK glyph takes roughly two.
    usable = max(1, width - 4)
    return sum(max(1, ceil(sum(2 if east_asian_width(ch) in {"W", "F"} else 1 for ch in line) / usable))
               for line in value.split("\n"))


def worksheet(number, results):
    headers = ["源文件", "缺失数据", "对应原始话题", "缺 state 的视频帧及同帧 action 情况"]
    widths = [43, 67, 56, 85]
    extra_columns = {}
    expanded = []
    for source_row in results:
        row = list(source_row)
        for index, value in enumerate(source_row):
            head, *remainder = excel_chunks(value)
            row[index] = head
            for part, text in enumerate(remainder, 2):
                key = (index, part)
                if key not in extra_columns:
                    extra_columns[key] = len(headers)
                    headers.append(f"{headers[index]}（续 {part}）")
                    widths.append(widths[index])
                column = extra_columns[key]
                row.extend([""] * (column + 1 - len(row)))
                row[column] = text
        expanded.append(row)
    rows = [
        [f"第 {number} 次上传 · 录制缺失清单（质检未通过或处理失败 {len(results)} 条）"],
        [
            "按当前质检或处理结果列出失败项。视频帧从 0 起。"
        ],
        headers,
        *expanded,
    ]
    sheet = [
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>',
        "<cols>"
        + "".join(
            f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths, 1)
        )
        + "</cols><sheetData>",
    ]
    for n, row in enumerate(rows, 1):
        lines = max(wrapped_lines(v, widths[i]) for i, v in enumerate(row)) if n > 3 else 1
        height = min(409, max(23, 18 * lines + 7))
        sheet.append(f'<row r="{n}" ht="{height}" customHeight="1">')
        for j, value in enumerate(row):
            if not value:
                continue
            ref = f"{column_name(j + 1)}{n}"
            style = 1 if n in (1, 3) else 0
            sheet.append(
                f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_text(value)}</t></is></c>'
            )
        sheet.append("</row>")
    sheet.append(
        f'</sheetData><autoFilter ref="A3:{column_name(len(headers))}{len(rows)}"/><mergeCells count="2"><mergeCell ref="A1:D1"/><mergeCell ref="A2:D2"/></mergeCells></worksheet>'
    )
    return "".join(sheet)


def write_workbook(path, number, results):
    write_batch_workbook(path, {number: results})


def write_batch_workbook(path, reports):
    ns = "http://schemas.openxmlformats.org/"
    main = ns + "spreadsheetml/2006/main"
    rel = ns + "officeDocument/2006/relationships"

    def relationships(items):
        return (
            f'<Relationships xmlns="{ns}package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="{rel}/{kind}" Target="{target}"/>'
                for i, (kind, target) in enumerate(items, 1)
            )
            + "</Relationships>"
        )

    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        sheets = [(n, f"worksheets/sheet{i}.xml") for i, n in enumerate(reports, 1)]
        tabs = "".join(
            f'<sheet name="{("第" + str(n) + "批") if len(sheets) > 1 else "录制缺失清单"}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (n, _) in enumerate(sheets, 1)
        )
        types = [
            ("workbook.xml", "sheet.main"),
            *((name, "worksheet") for _, name in sheets),
            ("styles.xml", "styles"),
        ]
        z.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{ns}package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            + "".join(
                f'<Override PartName="/xl/{p}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{t}+xml"/>'
                for p, t in types
            )
            + "</Types>",
        )
        z.writestr("_rels/.rels", relationships([("officeDocument", "xl/workbook.xml")]))
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{tabs}</sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            relationships([*(("worksheet", name) for _, name in sheets), ("styles", "styles.xml")]),
        )
        for number, name in sheets:
            z.writestr(f"xl/{name}", worksheet(number, reports[number]))
        z.writestr(
            "xl/styles.xml",
            f'<styleSheet xmlns="{main}"><fonts count="2"><font><sz val="10"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF195365"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf><xf xfId="0" fontId="1" fillId="2" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        )
