#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parent

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

# Ziroom renders rent digits with a CSS sprite. The keys are CSS background
# x-positions from source.html; the values are the real digits shown there.
PRICE_POSITION_TO_DIGIT = {
    0: "8",
    -18: "6",
    -36: "5",
    -54: "2",
    -72: "0",
    -90: "3",
    -108: "9",
    -126: "1",
    -144: "4",
    -162: "7",
}

DEFAULT_BLOCKLIST_PATHS = (
    BASE_DIR / "preference/block.txt",
)
DEFAULT_FAVORITES_PATHS = (
    BASE_DIR / "preference/favorites.txt",
)
DEFAULT_RULES_PATHS = (
    BASE_DIR / "preference/rules.txt",
)
DEFAULT_COMMUTE_PATH = BASE_DIR / "通勤.json"
DEFAULT_LABELS_PATH = BASE_DIR / "labels.txt"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_LINKS_PATH = BASE_DIR / "links.json"
DEFAULT_SOURCE_PATH = BASE_DIR / "source/自如网-租房信息网-提供地区的房屋合租信息及月租价格.html"

RULE_OPERATORS = ("<=", ">=", "==", "!=", "<", ">", "不包含", "包含")
DECIMAL_COLUMNS = {"面积(㎡)", "租金(元/月)", "单位面积价格", "小区地图最低价"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)

    def has_class(self, class_name: str) -> bool:
        return class_name in self.attrs.get("class", "").split()

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, Node):
                parts.append(child.text())
            else:
                parts.append(child)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def find_all(
        self, tag: str | None = None, class_name: str | None = None
    ) -> list["Node"]:
        matched: list[Node] = []
        tag_ok = tag is None or self.tag == tag
        class_ok = class_name is None or self.has_class(class_name)
        if tag_ok and class_ok:
            matched.append(self)
        for child in self.children:
            if isinstance(child, Node):
                matched.extend(child.find_all(tag=tag, class_name=class_name))
        return matched

    def first(self, tag: str | None = None, class_name: str | None = None) -> "Node | None":
        matches = self.find_all(tag=tag, class_name=class_name)
        return matches[0] if matches else None


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def parse_html(source: str) -> Node:
    parser = TreeBuilder()
    parser.feed(source)
    parser.close()
    return parser.root


def decode_price(item: Node) -> int | None:
    digits: list[str] = []
    for span in item.find_all(tag="span", class_name="price-n"):
        style = html.unescape(span.attrs.get("style", ""))
        match = re.search(r"background-position:\s*(-?\d+)px\s+center", style)
        if not match:
            continue
        x_position = int(match.group(1))
        digit = PRICE_POSITION_TO_DIGIT.get(x_position)
        if digit is None:
            raise ValueError(f"Unknown price background-position: {x_position}px")
        digits.append(digit)
    return int("".join(digits)) if digits else None


def split_name(name: str) -> tuple[str, str, str, str]:
    lease_type = ""
    rest = name
    if "·" in name:
        lease_type, rest = name.split("·", 1)

    community = rest
    layout = ""
    room = ""
    match = re.match(r"^(?P<community>.+?)(?P<layout>\d+居\+?)·(?P<room>.+)$", rest)
    if match:
        community = match.group("community")
        layout = match.group("layout")
        room = match.group("room")
    return lease_type, community, layout, room


def parse_area(area_text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", area_text)
    return float(match.group(0)) if match else None


def parse_floor(floor_text: str) -> tuple[int | None, int | None, str]:
    match = re.search(r"(\d+)\s*/\s*(\d+)层", floor_text)
    if not match:
        return None, None, ""
    current_floor = int(match.group(1))
    total_floor = int(match.group(2))
    return current_floor, total_floor, "✅" if current_floor == total_floor else ""


def parse_place(place_text: str) -> tuple[str, str, int | None]:
    match = re.search(r"距(.+?线)(.+?站)步行约(\d+)米", place_text)
    if not match:
        return "", "", None
    return match.group(1), match.group(2), int(match.group(3))


def discount_tags(tags: Iterable[str]) -> list[str]:
    return [
        tag
        for tag in tags
        if "立减" in tag or "折扣" in tag or re.search(r"\d+(?:\.\d+)?折", tag)
    ]


def product_version(tags: Iterable[str]) -> str:
    for tag in tags:
        if re.search(r"友家\d", tag) or tag in {"ZHOME", "曼舍"}:
            return tag
    return ""


def first_matching_tag(tags: Iterable[str], pattern: str) -> str:
    for tag in tags:
        if re.search(pattern, tag):
            return tag
    return ""


def node_text(parent: Node, class_name: str) -> str:
    node = parent.first(class_name=class_name)
    return node.text() if node else ""


def make_house_id(name: str, area_text: str) -> str:
    clean_name = re.sub(r"\s+", " ", name).strip()
    clean_area = re.sub(r"\s+", "", area_text).strip()
    return f"{clean_name}{clean_area}"


def ensure_unique_house_ids(rows: list[dict[str, object]]) -> None:
    grouped_rows: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped_rows.setdefault(str(row.get("房源ID", "")), []).append(row)

    for house_id, group in grouped_rows.items():
        if len(group) == 1:
            continue
        for row in group:
            floor = re.sub(r"\s+", "", str(row.get("楼层") or ""))
            row["房源ID"] = f"{house_id}{floor}" if floor else house_id


def normalize_match_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def close_enough_key(entry: str, value: str) -> bool:
    if not entry or not value or abs(len(entry) - len(value)) > 1:
        return False
    if min(len(entry), len(value)) < 4:
        return False
    same_chars = sum(1 for left, right in zip(entry, value) if left == right)
    return same_chars / max(len(entry), len(value)) >= 0.75


def find_commute_value(row: dict[str, object], commute_data: dict[str, object]) -> object | None:
    searchable_values = (
        row.get("小区/公寓"),
        row.get("房源ID"),
        row.get("名称"),
        row.get("位置/地铁"),
    )
    searchable_texts = [normalize_match_text(value) for value in searchable_values if value]
    community = normalize_match_text(row.get("小区/公寓"))

    for key, value in commute_data.items():
        normalized_key = normalize_match_text(key)
        if not normalized_key:
            continue
        if any(
            normalized_key == text
            or normalized_key in text
            or text in normalized_key
            for text in searchable_texts
        ):
            return value
        if close_enough_key(normalized_key, community):
            return value
    return None


def add_commute_values(rows: list[dict[str, object]], commute_data: dict[str, object]) -> None:
    for row in rows:
        row["骑行通勤"] = find_commute_value(row, commute_data)


def extract_building_summaries(root: Node) -> dict[str, dict[str, int]]:
    summaries: dict[str, dict[str, int]] = {}
    for node in root.find_all(class_name="building-name"):
        match = re.match(r"(.+?) ¥(\d+)起（(\d+)套）", node.text())
        if not match:
            continue
        summaries[match.group(1)] = {
            "小区地图最低价": int(match.group(2)),
            "小区地图房源数": int(match.group(3)),
        }
    return summaries


def find_building_summary(
    row: dict[str, object],
    summaries: dict[str, dict[str, int]],
) -> dict[str, int] | None:
    community = normalize_match_text(row.get("小区/公寓"))
    for name, summary in summaries.items():
        normalized_name = normalize_match_text(name)
        if (
            normalized_name == community
            or normalized_name in community
            or community in normalized_name
            or close_enough_key(normalized_name, community)
        ):
            return summary
    return None


def add_building_summaries(
    rows: list[dict[str, object]],
    summaries: dict[str, dict[str, int]],
) -> None:
    for row in rows:
        summary = find_building_summary(row, summaries)
        row["小区地图最低价"] = summary["小区地图最低价"] if summary else None
        row["小区地图房源数"] = summary["小区地图房源数"] if summary else None


def add_label_marks(rows: list[dict[str, object]], labels: Iterable[str]) -> None:
    label_list = list(labels)
    for row in rows:
        tags = [tag.strip() for tag in str(row.get("标签") or "").split("、") if tag.strip()]
        for label in label_list:
            row[label] = "✅" if any(label in tag or tag in label for tag in tags) else ""


def extract_rows(root: Node) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in root.find_all(class_name="list-item-container"):
        name = node_text(item, "name")
        lease_type, community, layout, room = split_name(name)

        details = [node.text() for node in item.find_all(tag="span", class_name="area")]
        area_text = details[0] if len(details) > 0 else ""
        floor = details[1] if len(details) > 1 else ""
        direction = details[2] if len(details) > 2 else ""

        image = item.first(tag="img", class_name="ant-image-img")
        image_url = image.attrs.get("src", "") if image else ""

        tags = [tag.text() for tag in item.find_all(class_name="zr-tag") if tag.text()]
        status = node_text(item, "label-tag")
        checkin = node_text(item, "label-checkin")
        area = parse_area(area_text)
        price = decode_price(item)
        unit_area_price = round(price / area, 2) if price and area else None
        place = node_text(item, "place")
        house_id = make_house_id(name, area_text)
        current_floor, total_floor, is_top_floor = parse_floor(floor)
        subway_line, subway_station, walk_distance = parse_place(place)
        discount = discount_tags(tags)
        max_lease_until = first_matching_tag(tags, r"最多签至\d{4}/\d{1,2}/\d{1,2}")
        checkin_date_match = re.search(r"预计(.+?)可入住", checkin)
        checkin_date = checkin_date_match.group(1) if checkin_date_match else ""
        price_wrapper = item.first(class_name="price-wrapper")
        price_style = price_wrapper.attrs.get("style", "") if price_wrapper else ""

        rows.append(
            {
                "_链接匹配ID": house_id,
                "房源ID": house_id,
                "名称": name,
                "租住类型": lease_type,
                "小区/公寓": community,
                "户型": layout,
                "房间": room,
                "面积(㎡)": area,
                "楼层": floor,
                "当前楼层": current_floor,
                "总楼层": total_floor,
                "是否顶层": is_top_floor,
                "朝向": direction,
                "位置/地铁": place,
                "地铁线": subway_line,
                "地铁站": subway_station,
                "步行距离(米)": walk_distance,
                "租金(元/月)": price,
                "单位面积价格": unit_area_price,
                "优惠信息": "、".join(discount),
                "是否优惠": "✅" if discount else "",
                "产品版本": product_version(tags),
                "是否可短签": "✅" if any("可短签" in tag for tag in tags) else "",
                "是否价格标红": "✅" if "224, 56, 16" in price_style else "",
                "标签": "、".join(tags),
                "状态": status,
                "是否可预订": "✅" if status == "可预订" else "",
                "入住时间": checkin,
                "预计可入住日期": checkin_date,
                "最多签至": max_lease_until.replace("最多签至", ""),
                "图片链接": image_url,
            }
        )
    ensure_unique_house_ids(rows)
    return rows


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def clean_xml_text(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(
        char
        for char in text
        if char in "\t\n\r" or ord(char) >= 32
    )


def sheet_cell(ref: str, value: object, style: int | None = None) -> str:
    style_attr = f' s="{style}"' if style is not None else ""
    if value is None:
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, int):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    if isinstance(value, float):
        return f'<c r="{ref}"{style_attr}><v>{value:g}</v></c>'

    safe_value = html.escape(clean_xml_text(value), quote=False)
    return (
        f'<c r="{ref}" t="inlineStr"{style_attr}>'
        f"<is><t>{safe_value}</t></is></c>"
    )


def label_width(label: str) -> int:
    return max(10, min(24, len(label) * 2 + 4))


def build_hyperlinks_xml(headers: list[str], rows: list[dict[str, object]]) -> str:
    if "名称" not in headers:
        return ""

    link_column = column_name(headers.index("名称") + 1)
    links: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        if not row.get("房源链接"):
            continue
        links.append(f'<hyperlink ref="{link_column}{row_number}" r:id="rId{len(links) + 1}"/>')

    return f"<hyperlinks>{''.join(links)}</hyperlinks>" if links else ""


def build_sheet_relationships(rows: list[dict[str, object]]) -> str | None:
    links = [str(row.get("房源链接")) for row in rows if row.get("房源链接")]
    if not links:
        return None

    relationships = []
    for index, link in enumerate(links, start=1):
        target = html.escape(clean_xml_text(link), quote=True)
        relationships.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{target}" TargetMode="External"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(relationships)}</Relationships>"
    )


def build_sheet_xml(headers: list[str], rows: list[dict[str, object]], widths: list[int]) -> str:
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate(widths, start=1)
    )

    row_xml: list[str] = []
    header_cells = [
        sheet_cell(f"{column_name(col)}1", header, style=1)
        for col, header in enumerate(headers, start=1)
    ]
    row_xml.append('<row r="1">' + "".join(header_cells) + "</row>")

    for row_number, row in enumerate(rows, start=2):
        def cell_style(header: str) -> int:
            if header == "名称" and row.get("房源链接"):
                return 4
            if header in DECIMAL_COLUMNS:
                return 3
            return 2

        cells = [
            sheet_cell(
                f"{column_name(col)}{row_number}",
                row.get(header),
                style=cell_style(header),
            )
            for col, header in enumerate(headers, start=1)
        ]
        row_xml.append(f'<row r="{row_number}">' + "".join(cells) + "</row>")

    last_column = column_name(len(headers))
    last_row = max(len(rows) + 1, 1)
    dimension = f"A1:{last_column}{last_row}"
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="{dimension}"/>
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols}</cols>
  <sheetData>{''.join(row_xml)}</sheetData>
  <autoFilter ref="{dimension}"/>
  {build_hyperlinks_xml(headers, rows)}
</worksheet>"""


def build_headers(labels: list[str]) -> tuple[list[str], list[int]]:
    before_label_headers = [
        "rules",
        "名称",
        "房间",
        "租金(元/月)",
        "面积(㎡)",
        "单位面积价格",
        "楼层",
        "当前楼层",
        "总楼层",
        "是否顶层",
        "朝向",
        "小区/公寓",
        "小区地图最低价",
        "小区地图房源数",
        "租住类型",
        "户型",
        "位置/地铁",
        "地铁线",
        "地铁站",
        "步行距离(米)",
        "骑行通勤",
        "优惠信息",
        "是否优惠",
        "产品版本",
        "是否可短签",
        "是否价格标红",
        "标签",
    ]
    after_label_headers = [
        "状态",
        "是否可预订",
        "入住时间",
        "预计可入住日期",
        "最多签至",
        "图片链接",
        "block",
        "favorites",
        "房源ID",
    ]
    before_label_widths = [
        8, 30, 10, 14, 10, 14, 10, 10, 10, 10, 10, 20, 14, 14, 12, 10,
        36, 10, 16, 14, 14, 36, 10, 12, 12, 12, 50,
    ]
    after_label_widths = [10, 12, 18, 18, 16, 60, 8, 10, 36]
    return (
        before_label_headers + labels + after_label_headers,
        before_label_widths + [label_width(label) for label in labels] + after_label_widths,
    )


def write_xlsx(path: Path, rows: list[dict[str, object]], labels: list[str]) -> None:
    headers, widths = build_headers(labels)

    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="自如房源" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        "xl/styles.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="11"/><name val="Arial"/></font>
    <font><b/><sz val="11"/><name val="Arial"/></font>
    <font><u/><color rgb="FF0563C1"/><sz val="11"/><name val="Arial"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE9EEF3"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="5">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1">
      <alignment horizontal="center" vertical="center"/>
    </xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0">
      <alignment vertical="top" wrapText="1"/>
    </xf>
    <xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1">
      <alignment vertical="top" wrapText="1"/>
    </xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1">
      <alignment vertical="top" wrapText="1"/>
    </xf>
  </cellXfs>
</styleSheet>""",
        "xl/worksheets/sheet1.xml": build_sheet_xml(headers, rows, widths),
    }
    sheet_relationships = build_sheet_relationships(rows)
    if sheet_relationships:
        files["xl/worksheets/_rels/sheet1.xml.rels"] = sheet_relationships

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def csv_cell(value: object, header: str) -> str:
    if value is None:
        return ""
    if header in DECIMAL_COLUMNS:
        number = parse_number(value)
        return "" if number is None else f"{number:.2f}"
    return clean_xml_text(value)


def write_csv(path: Path, rows: list[dict[str, object]], labels: list[str]) -> None:
    headers, _ = build_headers(labels)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([csv_cell(row.get(header), header) for header in headers])


def read_source(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, "unsupported source encoding")


def read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return read_source(path)


def read_commute_data(path: Path) -> dict[str, object]:
    content = read_optional_text(path).strip()
    if not content:
        return {}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Commute file must be a JSON object: {path}")
    return {str(key): value for key, value in data.items()}


def read_links_data(path: Path) -> dict[str, str]:
    content = read_optional_text(path).strip()
    if not content:
        return {}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Links file must be a JSON object: {path}")
    return {str(key): str(value) for key, value in data.items()}


def add_house_links(rows: list[dict[str, object]], links_data: dict[str, str]) -> None:
    for row in rows:
        match_ids = (
            str(row.get("房源ID") or ""),
            str(row.get("_链接匹配ID") or ""),
        )
        row["房源链接"] = next(
            (links_data[match_id] for match_id in match_ids if match_id in links_data),
            "",
        )


def read_config(path: Path) -> dict[str, object]:
    content = read_optional_text(path).strip()
    if not content:
        return {}
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a JSON object: {path}")
    return data


def sort_key_part(value: object) -> tuple[int, int, float | str]:
    if value is None or value == "":
        return (1, 0, "")
    if isinstance(value, (int, float)):
        return (0, 0, float(value))

    text = str(value).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return (0, 0, float(text))
    return (0, 1, text)


def default_sort_columns(config: dict[str, object]) -> list[str]:
    value = config.get("默认排序")
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def sort_rows_by_config(rows: list[dict[str, object]], config: dict[str, object]) -> None:
    columns = default_sort_columns(config)
    if not columns:
        return
    unknown_columns = [column for column in columns if rows and column not in rows[0]]
    if unknown_columns:
        raise KeyError(f"Unknown default sort column: {', '.join(unknown_columns)}")
    rows.sort(key=lambda row: tuple(sort_key_part(row.get(column)) for column in columns))


def filter_rows_by_config(rows: list[dict[str, object]], config: dict[str, object]) -> list[dict[str, object]]:
    if config.get("只显示满足规则") is True:
        return [row for row in rows if row.get("rules") == "✅"]
    return rows


def read_preference_entries(paths: Iterable[Path]) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for line in read_optional_text(path).splitlines():
            entry = re.sub(r"\s+", " ", line).strip()
            if not entry or entry.startswith("#") or entry in seen:
                continue
            entries.append(entry)
            seen.add(entry)
    return entries


def normalize_rule_text(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", "", text).replace("卧室", "卧")


def parse_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def parse_rule(line: str) -> tuple[str, str, str]:
    for operator in RULE_OPERATORS:
        if operator in line:
            field, expected = line.split(operator, 1)
            field = field.strip()
            expected = expected.strip().rstrip("。；;")
            if not field or not expected:
                raise ValueError(f"Invalid rule: {line}")
            return field, operator, expected
    raise ValueError(f"Rule must contain an operator: {line}")


def read_rules(paths: Iterable[Path]) -> list[tuple[str, str, str]]:
    return [parse_rule(entry) for entry in read_preference_entries(paths)]


def matches_rule(row: dict[str, object], rule: tuple[str, str, str]) -> bool:
    field, operator, expected = rule
    if field not in row:
        raise KeyError(f"Unknown rule field: {field}")

    actual = row.get(field)
    if operator in {"<", "<=", ">", ">="}:
        actual_number = parse_number(actual)
        expected_number = parse_number(expected)
        if actual_number is None or expected_number is None:
            return False
        if operator == "<":
            return actual_number < expected_number
        if operator == "<=":
            return actual_number <= expected_number
        if operator == ">":
            return actual_number > expected_number
        return actual_number >= expected_number

    actual_text = normalize_rule_text(actual)
    expected_text = normalize_rule_text(expected)
    if operator == "==":
        return actual_text == expected_text
    if operator == "!=":
        return actual_text != expected_text
    if operator == "包含":
        return expected_text in actual_text
    if operator == "不包含":
        return expected_text not in actual_text
    raise ValueError(f"Unsupported rule operator: {operator}")


def add_rule_marks(
    rows: list[dict[str, object]],
    rules: Iterable[tuple[str, str, str]],
) -> None:
    rule_list = list(rules)
    for row in rows:
        row["rules"] = "✅" if rule_list and all(matches_rule(row, rule) for rule in rule_list) else ""


def row_matches_entries(row: dict[str, object], entries: Iterable[str]) -> bool:
    searchable_text = " ".join(
        re.sub(r"\s+", " ", str(value)).strip()
        for key, value in row.items()
        if key not in {"block", "favorites", "rules"} and not key.startswith("_") and value
    )
    return any(entry in searchable_text for entry in entries)


def add_preference_marks(
    rows: list[dict[str, object]],
    block_entries: Iterable[str],
    favorite_entries: Iterable[str],
) -> None:
    for row in rows:
        row["block"] = "❌" if row_matches_entries(row, block_entries) else ""
        row["favorites"] = "⭐" if row_matches_entries(row, favorite_entries) else ""


def csv_output_path(output_path: Path) -> Path:
    return output_path.with_suffix(".csv")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Ziroom saved HTML to Excel and CSV.")
    parser.add_argument(
        "--blocklist",
        action="append",
        type=Path,
        help="blacklist file; can be passed more than once",
    )
    parser.add_argument(
        "--favorites",
        action="append",
        type=Path,
        help="favorites file; can be passed more than once",
    )
    parser.add_argument(
        "--rules",
        action="append",
        type=Path,
        help="rule file; can be passed more than once",
    )
    parser.add_argument(
        "--commute",
        type=Path,
        default=DEFAULT_COMMUTE_PATH,
        help="commute JSON file",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_LABELS_PATH,
        help="labels file",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="config JSON file",
    )
    parser.add_argument(
        "--links",
        type=Path,
        default=DEFAULT_LINKS_PATH,
        help="house links JSON file keyed by 房源ID",
    )
    parser.add_argument("source", nargs="?", default=DEFAULT_SOURCE_PATH, help="HTML file path")
    parser.add_argument(
        "output",
        nargs="?",
        default="ziroom_houses.xlsx",
        help="output .xlsx path; a .csv with the same base name is also exported",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    source_path = Path(args.source)
    output_path = Path(args.output)

    root = parse_html(read_source(source_path))
    rows = extract_rows(root)
    if not rows:
        print(f"No house rows found in {source_path}", file=sys.stderr)
        return 1

    block_paths = args.blocklist or DEFAULT_BLOCKLIST_PATHS
    favorite_paths = args.favorites or DEFAULT_FAVORITES_PATHS
    rule_paths = args.rules or DEFAULT_RULES_PATHS
    config = read_config(args.config)
    labels = read_preference_entries([args.labels])
    add_commute_values(rows, read_commute_data(args.commute))
    add_building_summaries(rows, extract_building_summaries(root))
    add_house_links(rows, read_links_data(args.links))
    add_label_marks(rows, labels)
    add_preference_marks(
        rows,
        read_preference_entries(block_paths),
        read_preference_entries(favorite_paths),
    )
    add_rule_marks(rows, read_rules(rule_paths))
    rows = filter_rows_by_config(rows, config)
    sort_rows_by_config(rows, config)
    write_xlsx(output_path, rows, labels)
    csv_path = csv_output_path(output_path)
    write_csv(csv_path, rows, labels)
    print(f"Exported {len(rows)} rows to {output_path} and {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
