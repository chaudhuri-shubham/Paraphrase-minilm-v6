"""
Dummy GRC (Governance, Risk & Control) data generator.

Simulates a realistic tabular export you'd get from a GRC tool:
Risk Taxonomy -> Issue -> Control, with free-text detail fields.

No special/hard-to-install packages used - just pandas (stdlib-adjacent, ubiquitous).
This is deliberately written so you can swap `build_dummy_dataframe()` for a
`pd.read_csv("your_export.csv")` / `pd.read_excel(...)` call later and everything
downstream (theme tagging, gap detection, graph building) keeps working as long
as your real columns are renamed to match the schema below.

Schema (one row per issue):
    issue_id        : str   e.g. "ISS-1001"
    issue_title      : str
    issue_details    : str  (free text - themes are derived from this)
    risk_taxonomy    : str  (top-level risk category)
    risk_id          : str
    control_id       : str or None   (None => control gap)
    control_title    : str or None
    control_details  : str or None
"""

import json
import random
import re
from collections import Counter

import pandas as pd

random.seed(42)

# ---------------------------------------------------------------------------
# 1. Reference data: risk taxonomy + a pool of realistic issue/control text
# ---------------------------------------------------------------------------

RISK_TAXONOMY = [
    ("RT-01", "Operational Risk"),
    ("RT-02", "Third-Party / Vendor Risk"),
    ("RT-03", "Cyber & Information Security Risk"),
    ("RT-04", "Regulatory Compliance Risk"),
]

# Each entry: issue title/details text, which risk taxonomy it rolls up to,
# and (optionally) a control. Leave control fields as None to simulate a gap.
ISSUE_POOL = [
    dict(risk="RT-01", title="Manual reconciliation errors in month-end close",
         details="Finance team performs month-end reconciliations manually in spreadsheets, "
                 "leading to recurring posting errors and delayed close cycles.",
         control="Automated reconciliation workflow with exception reporting"),
    dict(risk="RT-01", title="Segregation of duties gap in payment processing",
         details="Same user can both initiate and approve vendor payments in the ERP, "
                 "creating a segregation of duties conflict.",
         control="Maker-checker approval control on payment initiation"),
    dict(risk="RT-01", title="Undocumented process for exception approvals",
         details="Exception approvals for write-offs above threshold are handled ad hoc over "
                 "email with no standard workflow or audit trail.",
         control=None),
    dict(risk="RT-01", title="Backup job failures not monitored",
         details="Nightly backup jobs for the core banking application have failed "
                 "intermittently over the past quarter without alerting.",
         control="Automated backup monitoring and failure alerting"),
    dict(risk="RT-02", title="Vendor due diligence not refreshed periodically",
         details="Critical third-party vendors have not had their due diligence "
                 "questionnaires refreshed in over 24 months.",
         control="Annual vendor due-diligence refresh cycle"),
    dict(risk="RT-02", title="Fourth-party risk not assessed",
         details="Vendors' own subcontractors (fourth parties) handling customer data are "
                 "not identified or assessed as part of onboarding.",
         control=None),
    dict(risk="RT-02", title="Vendor SLA breaches not tracked centrally",
         details="Service level breaches reported by business units against key vendors are "
                 "tracked in disparate local trackers, not a central register.",
         control="Centralized vendor SLA / performance dashboard"),
    dict(risk="RT-02", title="Offboarding of terminated vendor access delayed",
         details="Access revocation for vendor staff after contract termination has taken "
                 "up to 30 days in several instances.",
         control=None),
    dict(risk="RT-03", title="Privileged access reviews overdue",
         details="Quarterly privileged/admin access recertification for core systems has "
                 "been overdue for two consecutive cycles.",
         control="Quarterly privileged access recertification"),
    dict(risk="RT-03", title="Shared service accounts without MFA",
         details="Several shared service accounts used by batch jobs do not have multi-factor "
                 "authentication or password rotation enabled.",
         control=None),
    dict(risk="RT-03", title="Endpoint patching SLA breaches",
         details="Critical severity vulnerability patches are being applied outside the "
                 "15-day SLA on a recurring set of endpoints.",
         control="Automated patch compliance monitoring"),
    dict(risk="RT-03", title="Data loss prevention rules not tuned",
         details="DLP tooling generates high false-positive volumes, causing analysts to "
                 "de-prioritize alerts, some of which involve sensitive data egress.",
         control="DLP rule tuning and alert triage workflow"),
    dict(risk="RT-03", title="Departed employee access not deprovisioned same-day",
         details="Access deprovisioning for terminated employees is not consistently "
                 "completed on the last working day across all systems.",
         control=None),
    dict(risk="RT-04", title="Regulatory reporting figures not independently validated",
         details="Key regulatory submission figures are compiled by a single analyst with "
                 "no independent second-line validation before submission.",
         control="Independent second-line validation of regulatory filings"),
    dict(risk="RT-04", title="Policy attestation completion rate low",
         details="Annual compliance policy attestation completion rate across the "
                 "business fell below the 95% target this cycle.",
         control="Automated attestation reminders and escalation"),
    dict(risk="RT-04", title="Complaints root-cause analysis not evidenced",
         details="Customer complaint files reviewed show resolution notes but no "
                 "documented root-cause analysis or trend reporting to management.",
         control=None),
]

# Theme keyword rules used to auto-tag a theme from issue_details text.
# (A simple stand-in for whatever NLP / manual theme tagging you're doing.)
THEME_RULES = [
    ("Manual Process & Automation Gaps", ["manual", "spreadsheet", "ad hoc", "workflow"]),
    ("Access & Identity Management", ["access", "privileged", "mfa", "multi-factor", "authentication", "deprovision", "segregation"]),
    ("Third-Party Oversight", ["vendor", "third-party", "fourth-part", "subcontractor", "sla"]),
    ("Monitoring & Alerting", ["monitor", "alert", "backup", "patch"]),
    ("Regulatory & Reporting", ["regulatory", "attestation", "complaint", "submission", "policy"]),
    ("Data Protection", ["dlp", "data loss", "sensitive data"]),
]

# Probable-control suggestion library, keyed by theme, used when an issue has
# no control tagged yet (i.e. a control gap).
PROBABLE_CONTROL_BY_THEME = {
    "Manual Process & Automation Gaps": "Introduce a system-enforced workflow with "
        "approval steps to replace the manual/email-based process.",
    "Access & Identity Management": "Implement automated joiner-mover-leaver access "
        "revocation tied to HR/vendor offboarding triggers.",
    "Third-Party Oversight": "Extend vendor risk assessment to cover subcontractor "
        "(fourth-party) data handling as part of onboarding and periodic review.",
    "Monitoring & Alerting": "Deploy automated monitoring with threshold-based alerting "
        "and a defined escalation path.",
    "Regulatory & Reporting": "Add a documented root-cause and trend analysis step to "
        "the existing review process.",
    "Data Protection": "Tune detection rules and add a triage workflow to reduce noise "
        "on sensitive-data alerts.",
}


def tag_theme(details: str):
    """Returns (primary_theme, possible_themes) — an issue's wording can
    plausibly match more than one theme; we keep the strongest match as
    primary but surface all matches so a reviewer can see the alternatives."""
    text = details.lower()
    scores = Counter()
    for theme, keywords in THEME_RULES:
        for kw in keywords:
            if re.search(kw, text):
                scores[theme] += 1
    if not scores:
        return "General / Unclassified", ["General / Unclassified"]
    ranked = [t for t, _ in scores.most_common()]
    return ranked[0], ranked


def build_dummy_dataframe() -> pd.DataFrame:
    rows = []
    issue_seq, control_seq = 1000, 500
    for item in ISSUE_POOL:
        issue_seq += 1
        issue_id = f"ISS-{issue_seq}"
        theme, possible_themes = tag_theme(item["details"])

        control_id, control_title, control_details = None, None, None
        if item["control"]:
            control_seq += 1
            control_id = f"CTL-{control_seq}"
            control_title = item["control"]
            control_details = (
                f"{item['control']}, operated by the process owner and evidenced "
                f"through periodic testing."
            )

        rt_id, rt_name = next(r for r in RISK_TAXONOMY if r[0] == item["risk"])
        rows.append(dict(
            issue_id=issue_id,
            issue_title=item["title"],
            issue_details=item["details"],
            risk_id=rt_id,
            risk_taxonomy=rt_name,
            theme=theme,
            possible_themes="|".join(possible_themes),
            control_id=control_id,
            control_title=control_title,
            control_details=control_details,
        ))
    return pd.DataFrame(rows)


def build_graph_payload(df: pd.DataFrame) -> dict:
    """Convert the flat table into node/edge lists the HTML/JS graph consumes."""
    nodes, edges = [], []
    seen_risk = set()

    for _, row in df.iterrows():
        # Risk taxonomy node (dedup)
        if row.risk_id not in seen_risk:
            nodes.append({
                "id": row.risk_id,
                "label": row.risk_taxonomy,
                "group": "risk",
                "details": {
                    "type": "Risk Taxonomy",
                    "id": row.risk_id,
                    "name": row.risk_taxonomy,
                },
            })
            seen_risk.add(row.risk_id)

        has_gap = pd.isna(row.control_id)
        probable = PROBABLE_CONTROL_BY_THEME.get(row.theme, "Define a control to address this issue.")

        # Issue node
        nodes.append({
            "id": row.issue_id,
            "label": row.issue_title,
            "group": "issue_gap" if has_gap else "issue",
            "details": {
                "type": "Issue",
                "id": row.issue_id,
                "title": row.issue_title,
                "description": row.issue_details,
                "theme": row.theme,
                "possible_themes": row.possible_themes.split("|"),
                "risk_taxonomy": row.risk_taxonomy,
                "control_status": "GAP - No control mapped" if has_gap else "Mapped",
                "probable_control": probable if has_gap else None,
            },
        })
        edges.append({"from": row.risk_id, "to": row.issue_id, "label": "categorized under"})

        if has_gap:
            gap_node_id = f"GAP-{row.issue_id}"
            nodes.append({
                "id": gap_node_id,
                "label": "No Control Mapped",
                "group": "gap",
                "details": {
                    "type": "Control Gap",
                    "issue_id": row.issue_id,
                    "theme": row.theme,
                    "probable_control": probable,
                },
            })
            edges.append({"from": row.issue_id, "to": gap_node_id, "label": "control gap", "dashed": True})
        else:
            nodes.append({
                "id": row.control_id,
                "label": row.control_title,
                "group": "control",
                "details": {
                    "type": "Control",
                    "id": row.control_id,
                    "title": row.control_title,
                    "description": row.control_details,
                    "linked_issue": row.issue_id,
                    "linked_risk_taxonomy": row.risk_taxonomy,
                    "theme": row.theme,
                },
            })
            edges.append({"from": row.issue_id, "to": row.control_id, "label": "mitigated by"})

    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    df = build_dummy_dataframe()
    df.to_csv("/home/claude/grc_graph/dummy_grc_data.csv", index=False)
    payload = build_graph_payload(df)
    with open("/home/claude/grc_graph/graph_data.json", "w") as f:
        json.dump(payload, f, indent=2)

    total_issues = len(df)
    gaps = df["control_id"].isna().sum()
    print(f"Issues: {total_issues} | Controls mapped: {total_issues - gaps} | Gaps: {gaps}")
    print(df[["issue_id", "risk_taxonomy", "theme", "control_id"]].to_string(index=False)) 