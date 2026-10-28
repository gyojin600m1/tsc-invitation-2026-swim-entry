#!/usr/bin/env python3
"""大会前に公開された公式PDFから data/base.json を作る（第1段階のデータ）。

  python3 tools/prepare.py

- src/26.57invitation_number.pdf        … 選手番号一覧表（10/23公開）
- src/26.57invitation_swim_timetable.pdf … 競技日程（競技No.・組数・開始時刻）
- src/26.57invitation_expert_swim.pdf    … 招待選手出場予定

公式の集計値（二次要項）と合わなければ書き出さない。
"""
import collections, json, logging, pathlib, re, sys, unicodedata
import pdfplumber

logging.disable(logging.WARNING)
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

# 二次要項「参加状況」
OFFICIAL = {"swimmers": 3560, "teams": 387, "entries": 8881, "invited": 19, "invitedEntries": 78, "programs": 184}
DAYS = ["10/30(金)", "10/31(土)", "11/1(日)", "11/2(月)", "11/3(火・祝)"]
DAY_OF = [(1, 44), (45, 72), (73, 110), (111, 146), (147, 184)]


def fix(s):
    """部首の字形（⾧ U+2FA7 など）だけを通常の漢字に直す。半角カナは公式表記のまま残す。"""
    return "".join(unicodedata.normalize("NFKC", c) if 0x2F00 <= ord(c) <= 0x2FDF else c for c in s)


def key(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def parse_numbers():
    # 1ページ2段組み。列の左端: No <60 / 氏名 60-180 / 団体 180-300、右段は +290
    cols = [(0, 60, 180, 300), (300, 350, 470, 600)]
    rows = []
    with pdfplumber.open(SRC / "26.57invitation_number.pdf") as pdf:
        for page in pdf.pages:
            lines = collections.defaultdict(list)
            for w in page.extract_words():
                if w["top"] >= 40:
                    lines[round(w["top"])].append(w)
            for _, ws in sorted(lines.items()):
                for a, b, c, d in cols:
                    no = [w for w in ws if a <= w["x0"] < b]
                    if not no:
                        continue
                    name = " ".join(w["text"] for w in ws if b <= w["x0"] < c)
                    team = " ".join(w["text"] for w in ws if c <= w["x0"] < d)
                    rows.append({"no": int(no[0]["text"]), "name": fix(name), "team": fix(team)})
    rows.sort(key=lambda r: r["no"])
    nos = [r["no"] for r in rows]
    assert nos == list(range(1, len(nos) + 1)), "選手番号に欠けか重複がある"
    blank = [r["no"] for r in rows if not r["name"]]
    swimmers = [r for r in rows if r["name"]]
    assert all(r["team"] for r in swimmers)
    # 団体は番号順に連続して並ぶ。末尾（No.3558〜）は追加分で、既存団体の名前が再び出る。
    # それ以外で同じ略称が離れた2か所に出るのは別団体（「栄東」が No.371〜372 と No.648）。
    LATE_FROM = 3558
    teams, prev = [], None
    for s in swimmers:
        same = [t for t in teams if t["name"] == s["team"]] if s["no"] >= LATE_FROM else []
        if same:
            t = same[0]
        elif prev and prev["name"] == s["team"]:
            t = prev
        else:
            t = {"id": len(teams), "name": s["team"], "nos": []}
            teams.append(t)
        t["nos"].append(s["no"])
        s["tid"] = t["id"]
        prev = t if s["no"] < LATE_FROM else prev
    dup = collections.Counter(t["name"] for t in teams)
    for t in teams:
        if dup[t["name"]] > 1:
            t["note"] = "同じ略称の別団体（選手番号 %d〜）" % t["nos"][0]
    return swimmers, teams, blank


def parse_timetable():
    pat = re.compile(r"(\d+) (女子|男子) (\d+)ｍ (\S+) (予選|全年齢T決勝|決勝|9歳以下決勝|\d+〜\d+歳決勝) (\d+) (\d{1,2}:\d{2})")
    prog = {}
    with pdfplumber.open(SRC / "26.57invitation_swim_timetable.pdf") as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                for m in pat.finditer(line):
                    no, g, dist, stroke, rd, heats, start = m.groups()
                    no = int(no)
                    stroke = unicodedata.normalize("NFKC", stroke).replace("個人メドレー", "個人メドレー")
                    if rd == "予選":
                        rnd, cat = "予選", "全年齢"
                    elif rd == "全年齢T決勝":
                        rnd, cat = "タイム決勝", "全年齢"
                    elif rd == "決勝":
                        rnd, cat = "決勝", "全年齢"
                    else:
                        rnd, cat = "区分決勝", rd[:-2].replace("〜", "～")
                    day = next(i + 1 for i, (a, b) in enumerate(DAY_OF) if a <= no <= b)
                    prog[no] = {"no": no, "day": day, "gender": g, "distance": dist + "m", "stroke": stroke,
                                "round": rnd, "cat": cat, "heats": int(heats), "start": start}
    assert sorted(prog) == list(range(1, OFFICIAL["programs"] + 1)), sorted(set(range(1, 185)) - set(prog))
    # 決勝・区分決勝がどの予選から上がるか（同じ日・同じ性別・同じ種目の予選）
    for p in prog.values():
        if p["round"] in ("決勝", "区分決勝"):
            src = [q["no"] for q in prog.values() if q["round"] == "予選" and q["day"] == p["day"]
                   and (q["gender"], q["distance"], q["stroke"]) == (p["gender"], p["distance"], p["stroke"])]
            assert len(src) == 1, p
            p["from"] = src[0]
    return [prog[n] for n in sorted(prog)]


def parse_invited(swimmers):
    day_x = [291, 397, 504, 610, 716]
    by_name = collections.defaultdict(list)
    for s in swimmers:
        by_name[key(s["name"])].append(s)
    out = []
    with pdfplumber.open(SRC / "26.57invitation_expert_swim.pdf") as pdf:
        page = pdf.pages[0]
        lines = collections.defaultdict(list)
        for w in page.extract_words():
            lines[round(w["top"])].append(w)
        group = None
        rows = []
        for top in sorted(lines):
            ws = sorted(lines[top], key=lambda w: w["x0"])
            txt = "".join(w["text"] for w in ws)
            if "アジア競技大会" in txt:
                group = "アジア大会代表"
                continue
            if "ジュニアオリンピック" in txt:
                group = "JO最優秀・優秀選手"
                continue
            if ws[0]["x0"] < 50 and ws[0]["text"].isdigit() and len(ws) > 2:
                name = " ".join(w["text"] for w in ws if 50 <= w["x0"] < 120)
                aff = "".join(w["text"] for w in ws if 120 <= w["x0"] < 285)
                rows.append({"group": group, "name": name, "affiliation": aff, "days": [[] for _ in day_x], "top": top})
                cur = rows[-1]
            elif rows and top - rows[-1]["top"] > 4:
                continue
            else:
                cur = rows[-1] if rows else None
            if cur is None:
                continue
            for w in ws:
                if w["x0"] < 285:
                    continue
                d = min(range(5), key=lambda i: abs(day_x[i] - w["x0"]))
                for ev in w["text"].split("/"):
                    ev = unicodedata.normalize("NFKC", ev)
                    if ev and ev not in cur["days"][d]:
                        cur["days"][d].append(ev)
    for r in rows:
        hit = by_name.get(key(r["name"]), [])
        assert len(hit) == 1, (r["name"], hit)
        out.append({"no": hit[0]["no"], "group": r["group"], "affiliation": r["affiliation"], "days": r["days"]})
    return out


def main():
    swimmers, teams, blank = parse_numbers()
    program = parse_timetable()
    invited = parse_invited(swimmers)
    inv_entries = sum(len(d) for r in invited for d in r["days"])
    pre_heats = sum(p["heats"] for p in program if p["round"] in ("予選", "タイム決勝"))
    entry_events = [p for p in program if p["round"] in ("予選", "タイム決勝")]
    lo = sum(10 * (p["heats"] - 1) + 1 for p in entry_events)
    hi = sum(10 * p["heats"] for p in entry_events)

    checks = [
        ("選手", len(swimmers), OFFICIAL["swimmers"]),
        ("団体", len(teams), OFFICIAL["teams"]),
        ("競技No.", len(program), OFFICIAL["programs"]),
        ("招待選手", len(invited), OFFICIAL["invited"]),
        ("招待選手の種目", inv_entries, OFFICIAL["invitedEntries"]),
    ]
    print("── 公式の集計値との照合 ──────────────")
    ng = False
    for label, got, want in checks:
        ok = got == want
        ng |= not ok
        print("   %-14s %6d  %s" % (label, got, "✓" if ok else "✗ 公式は %d" % want))
    ok = lo <= OFFICIAL["entries"] <= hi
    ng |= not ok
    print("   予選+T決勝 %d組 → 申込は %d〜%d の範囲。公式 %d  %s" % (pre_heats, lo, hi, OFFICIAL["entries"], "✓" if ok else "✗"))
    print("   空欄の選手番号: %s" % blank)
    print("────────────────────────────────────")
    if ng:
        sys.exit("✗ 公式の集計値と合わないので書き出しません")

    base = {
        "meet": {
            "name": "第57回東京スイミングセンター優秀選手招待水泳競技大会",
            "short": "東京SC招待2026",
            "venue": "東京アクアティクスセンター",
            "days": DAYS,
            "lanes": 10,
            "numberListDate": "2026-10-23",
            # 二次要項「全体スケジュール」: [開門, 予選開始, 予選終了, 決勝開始, 決勝終了, 閉門]
            "daySchedule": [["7:10", "9:00", "14:57", "15:30", "17:53", "18:30"],
                            ["7:10", "9:00", "14:39", "15:10", "17:39", "18:20"],
                            ["7:10", "9:00", "14:43", "15:45", "17:40", "18:20"],
                            ["7:10", "9:00", "14:46", "15:15", "17:56", "18:30"],
                            ["7:10", "9:00", "13:40", "14:00", "16:01", "17:15"]],
            "dates": ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"],
            "official": OFFICIAL,
            "blankNumbers": blank,
        },
        "swimmers": [[s["no"], s["name"], s["tid"]] for s in swimmers],
        "teams": [{k: v for k, v in t.items() if k != "nos"} for t in teams],
        "program": program,
        "invited": invited,
    }
    out = ROOT / "data" / "base.json"
    out.write_text(json.dumps(base, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("✓ %s  (%.0f KB)" % (out.relative_to(ROOT), out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
