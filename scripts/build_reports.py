import os
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from config import *
from messages import CHEER_TEMPLATE, QUOTES
from users import USER_MAP

TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
OWNER, NAME = REPO.split("/")
API = f"https://api.github.com/repos/{OWNER}/{NAME}"
REPO_WEB = f"https://github.com/{OWNER}/{NAME}"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
}

KST = timezone(timedelta(hours=9))
STUDY_START = datetime.fromisoformat(STUDY_START_DATE).date()
SCORE_START = datetime.fromisoformat(SCORE_START_DATE).date()
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def name(user: str) -> str:
    return USER_MAP.get(user, user)


def is_excluded_user(user: str) -> bool:
    return user in EXCLUDED_USERS


def medal(rank_idx: int) -> str:
    if rank_idx == 0:
        return "🥇"
    if rank_idx == 1:
        return "🥈"
    if rank_idx == 2:
        return "🥉"
    return str(rank_idx + 1)


def issue_link(issue_num: int) -> str:
    return f"[#{issue_num}]({REPO_WEB}/issues/{issue_num})"


def to_kst(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(KST)


def paginate(url: str) -> list[dict]:
    items = []
    while url:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        items.extend(resp.json())
        url = resp.links["next"]["url"] if "next" in resp.links else None
    return items


def fetch_issues() -> list[dict]:
    return paginate(f"{API}/issues?state=all&per_page=100")


def fetch_comments(issue_number: int) -> list[dict]:
    return paginate(f"{API}/issues/{issue_number}/comments?per_page=100")


def extract_til(body: str) -> str:
    if not body:
        return ""

    til_match = re.search(r"(?im)^\s*#{1,6}\s*TIL\s*$", body)
    if not til_match:
        return ""

    start = til_match.end()

    next_section = re.search(r"(?im)^\s*#{1,6}\s*TMI\b.*$", body[start:])
    if next_section:
        content = body[start:start + next_section.start()]
    else:
        content = body[start:]

    return content.strip()


def format_til_markdown(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n")
    lines = text.rstrip().splitlines()

    result = []
    in_code_block = False

    for line in lines:
        stripped = line.rstrip()

        # code fence
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result.append(stripped)
            continue

        # inside code block: keep raw
        if in_code_block:
            result.append(line.rstrip())
            continue

        # blank line
        if stripped == "":
            result.append("")
            continue

        # markdown list / quote / task list
        if re.match(r"^(\s*[-*+]\s+|\s*\d+\.\s+|\s*>\s+|\s*-\s+\[[ xX]\])", stripped):
            result.append(stripped)
            continue

        # normal line => hard line break
        result.append(stripped + "  ")

    return "\n".join(result).rstrip()


def week_start(day):
    if isinstance(day, datetime):
        day = day.date()
    return day - timedelta(days=day.weekday())


def week_index_from_study(day) -> int:
    return ((week_start(day) - week_start(STUDY_START)).days // 7) + 1


def current_week_range(today):
    start = week_start(today)
    end = start + timedelta(days=6)
    return start, end


def longest_streak(days: set) -> int:
    if not days:
        return 0

    ordered = sorted(days)
    best = 1
    cur = 1

    for i in range(1, len(ordered)):
        if (ordered[i] - ordered[i - 1]).days == 1:
            cur += 1
        else:
            best = max(best, cur)
            cur = 1

    return max(best, cur)


def current_streak(days: set) -> int:
    today = datetime.now(KST).date()
    cur = 0
    probe = today

    while probe in days:
        cur += 1
        probe -= timedelta(days=1)

    return cur


def history(days: set, start_day, end_day, max_len=30) -> str:
    result = []
    d = start_day

    while d <= end_day:
        result.append("✅" if d in days else "⬜")
        d += timedelta(days=1)

    return "".join(result[-max_len:])


def time_bucket(hour: int) -> str:
    if 6 <= hour < 12:
        return "🌅 Morning (06-12)"
    if 12 <= hour < 18:
        return "☀️ Afternoon (12-18)"
    if 18 <= hour < 24:
        return "🌙 Evening (18-24)"
    return "🌃 Night (00-06)"


def format_week_cell(study_score: int, cheer_score: int) -> str:
    parts = []

    if study_score > 0:
        parts.append(f"✅+{study_score}")

    if cheer_score > 0:
        parts.append(f"💬+{cheer_score}")

    return " ".join(parts) if parts else "⬜"


def format_score_by_week_cell(
    study_score: int,
    cheer_score: int,
    pass_score: int,
    fail_score: int,
) -> str:
    parts = []

    if study_score > 0:
        parts.append(f"✅{study_score}")

    if cheer_score > 0:
        parts.append(f"💬{cheer_score}")

    if pass_score > 0:
        parts.append(f"🥳{pass_score}")

    if fail_score > 0:
        parts.append(f"😭{fail_score}")

    return " ".join(parts) if parts else "-"


def cheer_bot(
    issue_number: int,
    user: str,
    comments: list[dict],
    weekly_score: int,
    weekly_rank: int,
) -> None:
    for c in comments:
        if "cheer-bot" in c.get("body", ""):
            return

    quote = random.choice(QUOTES)

    body = CHEER_TEMPLATE.format(
        user=name(user),
        quote=quote,
        score=weekly_score,
        rank=weekly_rank,
    )

    requests.post(
        f"{API}/issues/{issue_number}/comments",
        headers=HEADERS,
        json={"body": body},
    ).raise_for_status()


def render_week_md(
    week_num: int,
    week_scores_for_week,
    week_day_scores_for_week,
    week_tils_for_week,
    all_users: list,
) -> str:
    start = week_start(STUDY_START) + timedelta(days=(week_num - 1) * 7)
    end = start + timedelta(days=6)

    weekly_total = {
        u: scores["study"] + scores["cheer"]
        for u, scores in week_scores_for_week.items()
    }

    users_for_week = sorted(
        all_users,
        key=lambda u: (-weekly_total.get(u, 0), u),
    )

    lines = []

    lines.append(f"# Week {week_num} ({start.isoformat()} ~ {end.isoformat()})\n\n")

    if weekly_total:
        weekly_mvp_user, weekly_mvp_score = max(
            weekly_total.items(),
            key=lambda x: x[1],
        )
        lines.append(
            f"## 🏅 Weekly MVP: {name(weekly_mvp_user)} "
            f"({weekly_mvp_score} points)\n\n"
        )

    lines.append("## 📆 Week Score\n\n")
    lines.append("| User | Mon | Tue | Wed | Thu | Fri | Sat | Sun | Total |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")

    for u in users_for_week:
        row = [name(u)]
        total_score = 0

        for wd in range(7):
            study_score = week_day_scores_for_week[u][wd]["study"]
            cheer_score = week_day_scores_for_week[u][wd]["cheer"]

            row.append(format_week_cell(study_score, cheer_score))
            total_score += study_score + cheer_score

        row.append(str(total_score))
        lines.append("| " + " | ".join(row) + " |\n")

    lines.append(
        "\n> ✅ Study +3 (하루 1회만 인정), "
        "💬 Cheer +1 (하루 최대 3점)\n\n"
    )

    lines.append("## 📚 TIL Summary\n\n")

    for idx_user, u in enumerate(users_for_week):
        lines.append(f"### 👤 {name(u)}\n\n")

        items = sorted(
            week_tils_for_week.get(u, []),
            key=lambda x: x["created"],
        )

        if not items:
            lines.append("- 이번 주 공부 인증 기록 없음\n\n")
        else:
            for idx_item, item in enumerate(items):
                lines.append(f"#### {item['title']}\n\n")

                formatted_body = format_til_markdown(item["body"])
                lines.append(formatted_body + "\n\n")

                if idx_item < len(items) - 1:
                    lines.append("##\n\n")

        if idx_user < len(users_for_week) - 1:
            lines.append("---\n\n")

    return "".join(lines)


def main():
    issues = fetch_issues()

    Path("reports").mkdir(exist_ok=True)

    total_scores = defaultdict(int)
    logs = defaultdict(list)

    study_days = defaultdict(set)

    study_count = defaultdict(int)
    cheer_user = defaultdict(int)

    stats = defaultdict(int)
    weekday_activity = defaultdict(int)
    time_activity = defaultdict(int)

    study_days_for_stats = defaultdict(set)

    cheer_count_by_day = defaultdict(int)
    cheer_once_per_issue = set()

    study_scored_days = set()

    issue_comments = {}

    weekly_scores = defaultdict(
        lambda: defaultdict(lambda: {"study": 0, "cheer": 0})
    )

    weekly_day_scores = defaultdict(
        lambda: defaultdict(
            lambda: {i: {"study": 0, "cheer": 0} for i in range(7)}
        )
    )

    weekly_tils = defaultdict(lambda: defaultdict(list))

    weekly_breakdown = defaultdict(
        lambda: defaultdict(
            lambda: {
                "study": 0,
                "cheer": 0,
                "pass": 0,
                "fail": 0,
            }
        )
    )

    today = datetime.now(KST).date()
    current_week_number = week_index_from_study(today)

    for issue in issues:
        labels = [l["name"] for l in issue.get("labels", [])]
        user = issue["user"]["login"]

        if is_excluded_user(user):
            continue

        issue_num = issue["number"]
        created_dt = to_kst(issue["created_at"])
        created_day = created_dt.date()

        # study
        if STUDY_LABEL in labels and created_day >= STUDY_START:
            study_days[user].add(created_day)

        if STUDY_LABEL in labels:
            if created_day >= STUDY_START:
                wk = week_index_from_study(created_day)

                til_text = extract_til(issue.get("body", ""))

                if til_text:
                    weekly_tils[wk][user].append(
                        {
                            "title": issue.get(
                                "title",
                                f"Issue #{issue_num}",
                            ),
                            "body": til_text,
                            "created": created_dt,
                        }
                    )

                study_day_key = (user, created_day)

                if study_day_key not in study_scored_days:
                    study_scored_days.add(study_day_key)

                    weekly_scores[wk][user]["study"] += STUDY_SCORE
                    weekly_day_scores[wk][user][created_dt.weekday()]["study"] += STUDY_SCORE
                    weekly_breakdown[user][wk]["study"] += STUDY_SCORE

            if created_day >= SCORE_START:
                total_day_key = ("study_total", user, created_day)

                if not hasattr(main, "_study_total_scored_days"):
                    main._study_total_scored_days = set()

                if total_day_key not in main._study_total_scored_days:
                    main._study_total_scored_days.add(total_day_key)

                    total_scores[user] += STUDY_SCORE
                    logs[user].append(
                        f"{created_day} study +{STUDY_SCORE} "
                        f"({issue_link(issue_num)})"
                    )

                    stats["study"] += 1
                    study_count[user] += 1

                    weekday_activity[created_dt.weekday()] += 1
                    time_activity[time_bucket(created_dt.hour)] += 1

                    study_days_for_stats[user].add(created_day)

        comments = fetch_comments(issue_num)
        issue_comments[issue_num] = comments

        for c in comments:
            cu = c["user"]["login"]

            if is_excluded_user(cu):
                continue

            if cu == user:
                continue

            c_dt = to_kst(c["created_at"])
            c_day = c_dt.date()

            once_key = (issue_num, cu)
            if once_key in cheer_once_per_issue:
                continue

            daily_key = (cu, c_day)
            if cheer_count_by_day[daily_key] >= CHEER_LIMIT:
                continue

            cheer_once_per_issue.add(once_key)
            cheer_count_by_day[daily_key] += 1

            if c_day >= STUDY_START:
                wk = week_index_from_study(c_day)

                weekly_scores[wk][cu]["cheer"] += CHEER_SCORE
                weekly_day_scores[wk][cu][c_dt.weekday()]["cheer"] += CHEER_SCORE
                weekly_breakdown[cu][wk]["cheer"] += CHEER_SCORE

            if c_day >= SCORE_START:
                total_scores[cu] += CHEER_SCORE
                logs[cu].append(
                    f"{c_day} cheer +{CHEER_SCORE} "
                    f"({issue_link(issue_num)})"
                )

                stats["cheer"] += 1
                cheer_user[cu] += 1

        # pass
        if PASS_LABEL in labels:
            if created_day >= SCORE_START:
                total_scores[user] += PASS_SCORE
                logs[user].append(
                    f"{created_day} cert-pass 🥳+{PASS_SCORE} "
                    f"({issue_link(issue_num)})"
                )
                stats["pass"] += 1

            if created_day >= STUDY_START:
                wk = week_index_from_study(created_day)
                weekly_breakdown[user][wk]["pass"] += PASS_SCORE

        # fail
        if FAIL_LABEL in labels:
            if created_day >= SCORE_START:
                total_scores[user] += FAIL_SCORE
                logs[user].append(
                    f"{created_day} cert-fail 😭+{FAIL_SCORE} "
                    f"({issue_link(issue_num)})"
                )
                stats["fail"] += 1

            if created_day >= STUDY_START:
                wk = week_index_from_study(created_day)
                weekly_breakdown[user][wk]["fail"] += FAIL_SCORE

    ranked_total = sorted(
        total_scores.items(),
        key=lambda x: (-x[1], x[0]),
    )

    current_week_total = {
        u: scores["study"] + scores["cheer"]
        for u, scores in weekly_scores[current_week_number].items()
    }

    ranked_weekly = sorted(
        current_week_total.items(),
        key=lambda x: (-x[1], x[0]),
    )

    weekly_rank_map = {
        u: i + 1
        for i, (u, _) in enumerate(ranked_weekly)
    }

    for issue in issues:
        labels = [l["name"] for l in issue.get("labels", [])]

        if STUDY_LABEL not in labels:
            continue

        user = issue["user"]["login"]

        if is_excluded_user(user):
            continue

        issue_num = issue["number"]
        comments = issue_comments.get(issue_num, [])

        cheer_bot(
            issue_num,
            user,
            comments,
            current_week_total.get(user, 0),
            weekly_rank_map.get(user, 0),
        )

    all_users = [
        u for u in USER_MAP
        if not is_excluded_user(u)
    ]

    # scoreboard.md
    scoreboard_lines = []
    scoreboard_lines.append("## 🏅 Award\n\n")

    if ranked_total:
        total_mvp_user, total_mvp_score = ranked_total[0]
        scoreboard_lines.append(
            f"- 🏆 Total MVP: {name(total_mvp_user)} "
            f"({total_mvp_score} points)\n"
        )

    if cheer_user:
        cheerful_user, cheerful_count = max(
            cheer_user.items(),
            key=lambda x: x[1],
        )
        scoreboard_lines.append(
            f"- 🎉 Most Cheerful: {name(cheerful_user)} "
            f"({cheerful_count} cheers)\n"
        )

    longest_user = None
    longest_value = 0

    for u, days in study_days.items():
        s = longest_streak(days)
        if s > longest_value:
            longest_user = u
            longest_value = s

    if longest_user:
        scoreboard_lines.append(
            f"- 🔥 Longest Streak: {name(longest_user)} "
            f"({longest_value} streaks)\n"
        )

    scoreboard_lines.append("\n## 📊 Score by Week\n\n")

    max_week = 0
    for u in weekly_breakdown:
        if weekly_breakdown[u]:
            max_week = max(
                max_week,
                max(weekly_breakdown[u].keys()),
            )

    header = (
        ["Rank", "User"]
        + [f"Week{i}" for i in range(1, max_week + 1)]
        + ["Total"]
    )

    scoreboard_lines.append(
        "| " + " | ".join(header) + " |\n"
    )

    scoreboard_lines.append(
        "|" + "|".join(["---"] * len(header)) + "|\n"
    )

    ordered_users = [u for u, _ in ranked_total]

    for i, u in enumerate(ordered_users):
        row = [medal(i), name(u)]

        for wk in range(1, max_week + 1):
            bd = weekly_breakdown[u][wk]

            row.append(
                format_score_by_week_cell(
                    bd["study"],
                    bd["cheer"],
                    bd["pass"],
                    bd["fail"],
                )
            )

        row.append(str(total_scores[u]))

        scoreboard_lines.append(
            "| " + " | ".join(row) + " |\n"
        )

    scoreboard_lines.append(
        "\n> "
        "✅ Study +3 (하루 1회만 인정), "
        "💬 Cheer +1 (하루 최대 3점), "
        "🥳 Cert Pass +10, "
        "😭 Cert Fail +5\n\n"
    )

    scoreboard_lines.append("## 🔥 Study History\n\n")
    scoreboard_lines.append("| User | Current | Best | Study History |\n")
    scoreboard_lines.append("|---|---|---|---|\n")

    history_start = STUDY_START
    history_end = today

    for u in ordered_users:
        days = study_days[u]

        cur = current_streak(days)
        best = longest_streak(days)
        hist = history(days, history_start, history_end)

        scoreboard_lines.append(
            f"| {name(u)} | {cur} | {best} | {hist} |\n"
        )

    Path("reports/scoreboard.md").write_text(
        "".join(scoreboard_lines),
        encoding="utf-8",
    )

    # weeks
    weeks_dir = Path("reports/weeks")
    weeks_dir.mkdir(parents=True, exist_ok=True)

    max_week_for_pages = max(weekly_scores.keys()) if weekly_scores else 0

    for wk in range(1, max_week_for_pages + 1):
        content = render_week_md(
            wk,
            weekly_scores[wk],
            weekly_day_scores[wk],
            weekly_tils[wk],
            all_users,
        )

        (weeks_dir / f"week{wk}.md").write_text(
            content,
            encoding="utf-8",
        )

    latest_week_content = render_week_md(
        current_week_number,
        weekly_scores[current_week_number],
        weekly_day_scores[current_week_number],
        weekly_tils[current_week_number],
        all_users,
    )

    Path("reports/weekly.md").write_text(
        latest_week_content,
        encoding="utf-8",
    )

    # log.md
    log_lines = ["# 🧾 Score Log\n\n"]

    for u, entries in sorted(
        logs.items(),
        key=lambda x: name(x[0]),
    ):
        log_lines.append(f"## 👤 {name(u)}\n\n")

        for entry in entries:
            log_lines.append(f"- {entry}\n")

        log_lines.append("\n---\n\n")

    Path("reports/log.md").write_text(
        "".join(log_lines),
        encoding="utf-8",
    )

    # stats.md
    pass_rate = 0
    if stats["pass"] + stats["fail"] > 0:
        pass_rate = round(
            stats["pass"]
            / (stats["pass"] + stats["fail"])
            * 100
        )
    
    total_members = len(all_users)
    max_week_for_stats = max(weekly_scores.keys()) if weekly_scores else 0
    
    stats_lines = []
    
    stats_lines.append("# 📊 Study Statistics\n\n")
    
    # 1. Activity
    stats_lines.append("## 📌 Activity\n\n")
    stats_lines.append(
        "| Study 인증 | Cheer 댓글 | 자격증 합격 | 자격증 불합격 |\n"
    )
    stats_lines.append("|:---:|:---:|:---:|:---:|\n")
    stats_lines.append(
        f"| {stats['study']} | {stats['cheer']} | "
        f"{stats['pass']} | {stats['fail']} |\n"
    )

    # 2. Certification Results
    stats_lines.append("\n## 🎓 Certification Results\n\n")
    stats_lines.append("| Pass | Fail | Pass Rate |\n")
    stats_lines.append("|:---:|:---:|:---:|\n")
    stats_lines.append(
        f"| {stats['pass']} | {stats['fail']} | "
        f"{pass_rate}% |\n"
    )
    
    # 3. Weekly Participation
    stats_lines.append("\n## 📈 Weekly Participation\n\n") 
    stats_lines.append("| Week | Participants | Rate |\n")
    stats_lines.append("|:---:|:---:|:---:|\n")
    
    for wk in range(1, max_week_for_stats + 1):
        participants = sum(
            1
            for u in all_users
            if weekly_breakdown[u][wk]["study"] > 0
        )
    
        rate = 0
        if total_members > 0:
            rate = round(participants / total_members * 100)
    
        stats_lines.append(
            f"| Week{wk} | {participants} | {rate}% |\n"
        )

    stats_lines.append(f"> 전체 기준 인원: {total_members}명\n")
    stats_lines.append("> 참여 인원: 해당 주차 스터디 인증 1회 이상 인원\n\n")
    
    # 4. Study Activity by Weekday
    stats_lines.append("\n## 📅 Study Activity by Weekday\n\n")
    stats_lines.append("| Mon | Tue | Wed | Thu | Fri | Sat | Sun |\n")
    stats_lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n")
    stats_lines.append(
        f"| {weekday_activity[0]} | {weekday_activity[1]} | "
        f"{weekday_activity[2]} | {weekday_activity[3]} | "
        f"{weekday_activity[4]} | {weekday_activity[5]} | "
        f"{weekday_activity[6]} |\n"
    )
    
    # 5. Study Activity by Time (4행 2열)
    stats_lines.append("\n## ⏰ Study Activity by Time\n\n")
    stats_lines.append("| Time | Count |\n")
    stats_lines.append("|:---|:---:|\n")
    stats_lines.append(
        f"| 🌅 Morning (06-12) | {time_activity['🌅 Morning (06-12)']} |\n"
    )
    stats_lines.append(
        f"| ☀️ Afternoon (12-18) | {time_activity['☀️ Afternoon (12-18)']} |\n"
    )
    stats_lines.append(
        f"| 🌙 Evening (18-24) | {time_activity['🌙 Evening (18-24)']} |\n"
    )
    stats_lines.append(
        f"| 🌃 Night (00-06) | {time_activity['🌃 Night (00-06)']} |\n"
    )
    
    Path("reports/stats.md").write_text(
        "".join(stats_lines),
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
