#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

SOURCE_FILE="${1:-source/自如网-租房信息网-提供地区的房屋合租信息及月租价格.html}"
OUTPUT_FILE="${2:-ziroom_houses.xlsx}"

if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "找不到输入文件: $SOURCE_FILE" >&2
  exit 1
fi

python3 export_ziroom_excel.py "$SOURCE_FILE" "$OUTPUT_FILE"
echo "完成: $OUTPUT_FILE"
