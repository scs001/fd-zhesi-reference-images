#!/usr/bin/env python3
"""probe_survey.py — PhilPapers Survey 试摄取解析器（zhesi-worldview-workshop task 1.2）.

Parses the local probe artifacts under data/raw/philpapers/ into the
survey_parsed.json contract ({"round", "source_url", "snapshot_ts", "questions"}).

Inputs (all already on disk; fetching happened during the probe, see docs/survey-probe.md):
  1. 2009_results_20230424171640.md  — markdown rendering of the Wayback capture of
     https://philpapers.org/surveys/results.pl (ts 20230424171640), 31 question
     blocks (the paper's 30 questions plus a second trolley variant), counts +
     percentages over N=931 (preliminary-wave tabulation).
  2. philbench_questions.json — QUESTIONS array extracted from PhilBench's
     index.html (github.com/lordscottish12/PhilBench): 100 questions with
     phil_dist (2020 round, official site survey2020.philpeople.org, N=1785)
     and phil_dist_2009 (final published 2009 paper tabulation) for the 30
     carried-over questions. Cross-validation source (Route 3).

Outputs:
  data/raw/philpapers/survey_parsed.json      — 2009 round (primary, Wayback-sourced)
  data/raw/philpapers/survey_parsed_2020.json — 2020 round (as much as resolvable)

Usage:
  python3 scripts/probe_survey.py [--datadir data/raw/philpapers]

Checks performed:
  - every parsed question's counts sum to the stated N (931) for the 2009 round
  - counts/N reproduce the page's printed percentages within rounding tolerance
  - 2009 percentages cross-checked against PhilBench phil_dist_2009 (paper tabulation):
    deltas reported (expected <= ~1.5pp; the two tabulations differ by construction)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

N_2009 = 931          # respondents, preliminary-wave tabulation on results.pl
N_2020 = 1785         # respondents, 2020 round (per official site / PhilBench README)

# topic aliases: wayback results.pl wording -> PhilBench topic wording
TOPIC_ALIASES = {
    "politics": "political philosophy",
    "trolley problem (five straight ahead, one on side track, turn requires switching)":
        "trolley problem (five straight ahead, one on side track, turn requires switching, what ought one do?)",
}

QUESTION_RE = re.compile(r"^#\s+(.+?)\s*$")
ROW_RE = re.compile(
    r"^\|\s*(Accept or lean toward:\s*|Other)\s*(.*?)\s*\|"
    r"\s*(\d+)\s*/\s*(\d+)\s*\(([\d.]+)%\)\s*\|\s*$"
)


def parse_wayback_markdown(path: Path) -> list[dict]:
    """Parse the markdown rendering of the 2009 results.pl Wayback capture."""
    questions: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        qm = QUESTION_RE.match(line)
        if qm:
            title = qm.group(1).strip()
            # skip the provenance header block: real questions end with '?'
            # and never look like markdown metadata headers we wrote
            if not title.endswith("?"):
                current = None
                continue
            current = {"question": title, "options": []}
            questions.append(current)
            continue
        rm = ROW_RE.match(line)
        if rm and current is not None:
            prefix, label, count, total, pct = rm.groups()
            if prefix == "Other":
                opt_label = "other"
            else:
                opt_label = label if label else "other"
            current["options"].append(
                {
                    "label": opt_label,
                    "count": int(count),
                    "pct": float(pct),
                }
            )
    # sanity: drop question blocks with no option rows (e.g. stray headers)
    return [q for q in questions if q["options"]]


def validate_2009(questions: list[dict]) -> list[str]:
    """Counts must sum to N and reproduce printed pcts.

    The page truncates percentages instead of rounding (e.g. 309/931 -> 33.1
    where round-half-up gives 33.2), and one row (Mind/physicalism 519/931
    55.8%) does not equal count/N under either scheme (site-side denominator
    quirk). Tolerance is therefore 0.15pp, not exact-rounding.
    """
    problems = []
    for q in questions:
        total = sum(o["count"] for o in q["options"])
        if total != N_2009:
            problems.append(f"{q['question']}: counts sum {total} != {N_2009}")
        for o in q["options"]:
            expect = o["count"] / N_2009 * 100
            if abs(expect - o["pct"]) > 0.15:
                problems.append(
                    f"{q['question']} [{o['label']}]: pct {o['pct']} deviates from count/N {expect:.2f} by >0.15pp"
                )
    return problems


def load_philbench(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # accept either the pristine array or {"questions": [...]} wrapper
    return data["questions"] if isinstance(data, dict) else data


def topic_of(question_text: str) -> str:
    """'A priori knowledge: yes or no?' -> 'A priori knowledge'."""
    return question_text.split(":")[0].strip().rstrip("?")


def cross_check_2009(wayback_qs: list[dict], philbench: list[dict]) -> dict:
    """Compare Wayback preliminary-wave pcts against PhilBench's 2009 numbers.

    These are two different official tabulations of the 2009 survey:
    results.pl all-completed preliminary wave (N=931, what Wayback captured)
    vs the final published paper tabulation (target-faculty weighted, what
    PhilBench embeds). Agreement on some questions is sub-pp; on others
    (large 'other'/skip buckets) deltas are tens of pp. Reported, not gated.
    """
    pb_by_topic = {topic_of(q["question"]).lower(): q
                   for q in philbench if q.get("phil_dist_2009")}
    matched, deltas = 0, []
    for q in wayback_qs:
        topic_l = topic_of(q["question"]).lower()
        topic_l = TOPIC_ALIASES.get(topic_l, topic_l)
        pb = pb_by_topic.get(topic_l)
        if not pb:
            continue
        matched += 1
        pb2009 = pb["phil_dist_2009"]
        for o in q["options"]:
            pb_pct = pb2009.get(o["label"])
            if pb_pct is None:
                continue
            deltas.append(abs(pb_pct - o["pct"]))
    deltas.sort()
    n = len(deltas)
    med = deltas[n // 2] if n else 0.0
    return {
        "matched_questions": matched,
        "option_comparisons": n,
        "median_abs_delta_pp": round(med, 1),
        "max_abs_delta_pp": round(deltas[-1], 1) if n else 0.0,
        "note": "preliminary-wave (results.pl, N=931) vs final published paper tabulation; see docs/survey-probe.md",
    }


def build_2020(philbench: list[dict]) -> list[dict]:
    """2020 round from PhilBench phil_dist (cross-validation source, used as
    the 2020 content carrier because the Wayback 2020 page could not be
    rendered through the only reachable channel — see docs/survey-probe.md)."""
    out = []
    for q in philbench:
        dist = q.get("phil_dist")
        if not dist:
            continue
        opts = []
        for label in q.get("options", []):
            if label in dist:
                opts.append({"label": label, "pct": dist[label]})
        # some distributions carry keys beyond options (e.g. 'other')
        for label, pct in dist.items():
            if label not in q.get("options", []):
                opts.append({"label": label, "pct": pct})
        out.append(
            {
                "id": q.get("id"),
                "topic": q.get("topic"),
                "category": q.get("category"),
                "question": q["question"],
                "multi_select": q.get("is_multi", False),
                "options": opts,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datadir", default="data/raw/philpapers",
                    help="directory holding the probe artifacts (default: data/raw/philpapers)")
    args = ap.parse_args()
    d = Path(args.datadir)

    md_path = d / "2009_results_20230424171640.md"
    pb_path = d / "philbench_questions.json"

    qs2009 = parse_wayback_markdown(md_path)
    problems = validate_2009(qs2009)
    n_options_2009 = sum(len(q["options"]) for q in qs2009)

    philbench = load_philbench(pb_path)
    xcheck = cross_check_2009(qs2009, philbench)

    parsed_2009 = {
        "round": "2009",
        "source_url": "https://web.archive.org/web/20230424171640id_/https://philpapers.org/surveys/results.pl",
        "snapshot_ts": "20230424171640",
        "respondents": N_2009,
        "tabulation": "preliminary-wave results.pl (Wayback capture of philpapers.org/surveys/results.pl; 31 question blocks incl. both trolley variants; final published paper tabulation differs, see docs/survey-probe.md)",
        "questions": qs2009,
    }
    (d / "survey_parsed.json").write_text(
        json.dumps(parsed_2009, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    qs2020 = build_2020(philbench)
    parsed_2020 = {
        "round": "2020",
        "source_url": "https://github.com/lordscottish12/PhilBench (QUESTIONS embedded in index.html; official site: https://survey2020.philpeople.org/)",
        "snapshot_ts": "2026-10-20T23:37+08:00 (jsDelivr fetch of PhilBench index.html; Wayback capture 20240605075202 of the official results page located but not renderable through the probe channel)",
        "respondents": N_2020,
        "questions": qs2020,
    }
    (d / "survey_parsed_2020.json").write_text(
        json.dumps(parsed_2020, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"2009 round: {len(qs2009)} questions, {n_options_2009} options "
          f"(N={N_2009}); validation problems: {len(problems)}")
    for p in problems:
        print("  !!", p)
    print(f"2020 round: {len(qs2020)} questions, "
          f"{sum(len(q['options']) for q in qs2020)} options (N={N_2020})")
    print(f"cross-check vs PhilBench paper tabulation: {xcheck}")

    ok = len(qs2009) >= 10 and not problems
    print("GATE:", "PASS" if ok else "FAIL", "(>=10 questions and internal consistency)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
