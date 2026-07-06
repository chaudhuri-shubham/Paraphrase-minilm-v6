"""
build_dashboard.py
------------------
Turn YOUR OWN risk/issue/control CSV into the interactive dashboard HTML.

Usage:
    python3 build_dashboard.py my_data.csv
    python3 build_dashboard.py my_data.csv my_dashboard.html   # custom output name

Required CSV columns (case-sensitive):
    issue_id          e.g. "ISS-1001"
    issue_title       short issue name
    issue_details     free text description (themes are auto-tagged from this)
    risk_id           e.g. "RT-01"
    risk_taxonomy     top-level risk category name, e.g. "Operational Risk"
    control_id        control ID, or leave BLANK if no control is tagged yet (=> gap)
    control_title     control name, or blank
    control_details   control description, or blank

If your column names differ, either rename them in the CSV/Excel before running,
or edit the `pd.read_csv(...)` call below to add a `.rename(columns={...})` step.

Themes are derived automatically from keywords in `issue_details` using the same
THEME_RULES / PROBABLE_CONTROL_BY_THEME dictionaries as the dummy dataset in
grc_pipeline.py. Add your own keyword rules there if your issue text uses
different vocabulary than the built-in examples.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from grc_pipeline import tag_theme, build_graph_payload, PROBABLE_CONTROL_BY_THEME

REQUIRED_COLUMNS = [
    "issue_id", "issue_title", "issue_details",
    "risk_id", "risk_taxonomy",
    "control_id", "control_title", "control_details",
]

TEMPLATE_PATH = Path(__file__).parent / "dashboard_template.html"
PLACEHOLDER = "__GRAPH_DATA_JSON__"


def load_and_validate(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(
            f"Missing required column(s): {missing}\n"
            f"Found columns: {list(df.columns)}\n"
            f"See the schema documented at the top of build_dashboard.py."
        )

    # Blank control_id => treated as a control gap.
    df["control_id"] = df["control_id"].replace("", pd.NA)
    df["control_title"] = df["control_title"].where(df["control_id"].notna(), pd.NA)
    df["control_details"] = df["control_details"].where(df["control_id"].notna(), pd.NA)

    # Derive theme + possible_themes from issue_details text.
    themes, possible = [], []
    for text in df["issue_details"].fillna(""):
        primary, ranked = tag_theme(text)
        themes.append(primary)
        possible.append("|".join(ranked))
    df["theme"] = themes
    df["possible_themes"] = possible

    return df


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 build_dashboard.py <your_data.csv> [output.html]")

    csv_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "my_grc_dashboard.html"

    df = load_and_validate(csv_path)
    payload = build_graph_payload(df)

    if not TEMPLATE_PATH.exists():
        raise SystemExit(f"Template not found at {TEMPLATE_PATH}. Keep dashboard_template.html alongside this script.")

    html = TEMPLATE_PATH.read_text()
    if PLACEHOLDER not in html:
        raise SystemExit("Template is missing the data placeholder — has it been edited?")

    html = html.replace(PLACEHOLDER, json.dumps(payload), 1)
    Path(out_path).write_text(html)

    total_issues = len(df)
    gaps = df["control_id"].isna().sum()
    print(f"Built {out_path}")
    print(f"Issues: {total_issues} | Controls mapped: {total_issues - gaps} | Gaps: {gaps}")


if __name__ == "__main__":
    main()