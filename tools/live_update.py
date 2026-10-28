#!/usr/bin/env python3
"""東京都水泳協会が種目ごとに出す「競技結果」「決勝スタートリスト」PDFを取り込み、index.html を作り直す。

  python3 tools/live_update.py                 # 本番（JSTの今の時刻で、出ているはずのPDFだけ確認）
  python3 tools/live_update.py --now 2026-10-30T23:00 --base https://tokyo-swim.org/result/2025/251030/ --out /tmp/x.json
                                              # 試験（昨年のPDFで動作確認。--out で本物の live.json を汚さない）

- PDFの場所: まず協会サイトの「競泳競技結果」記事を探し、そこに並ぶリンクを使う。
  記事がまだ無いときは昨年と同じ形 https://tokyo-swim.org/result/2026/261030/{S}NNN.pdf を試す。
- ETag で変化のないPDFは読み直さない（304）。
- 見出しの競技No.・性別・距離・種目が競技日程と1つでも違うPDFは取り込まない（前の内容を残す）。
- 予選・タイム決勝は「人数÷10 の切り上げ＝公式の組数」を確かめ、合わなければ警告に残す。
"""
import argparse, concurrent.futures, datetime, hashlib, html, json, math, pathlib, re, sys, unicodedata
import urllib.error, urllib.parse, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import seiko  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
JST = datetime.timezone(datetime.timedelta(hours=9))
MEET_DATES = [datetime.date(2026, 10, 30) + datetime.timedelta(days=i) for i in range(5)]
GUESS_BASE = "https://tokyo-swim.org/result/2026/261030/"
SEARCH = "https://tokyo-swim.org/?s=" + urllib.parse.quote("招待")
UA = {"User-Agent": "Mozilla/5.0 (tsc-invitation-2026 entry app; results importer)"}


def key(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def digits(s):
    return re.sub(r"\D", "", s or "")


def fetch(url, etag=None, timeout=30):
    """(状態, 中身, ETag, Last-Modified)。通信が切れた・時間切れのときは状態 0 を返す（例外を外に出さない）。
    1つのPDFが取れないだけで、その回のほかの取り込みまで止めないため。"""
    req = urllib.request.Request(url, headers=dict(UA, **({"If-None-Match": etag} if etag else {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("ETag"), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        return e.code, b"", None, None
    except Exception as e:  # URLError・timeout・接続リセット・途中で切れた読み込みなど
        return 0, repr(e).encode()[:200], None, None


def discover(state, log):
    """協会サイトの今年の「競泳競技結果」記事を探してPDFリンクを集める。見つからなければ None。"""
    post = state.get("post")
    cands = [post] if post else []
    if not post:
        st, body, _, _ = fetch(SEARCH)
        if st == 200:
            text = body.decode("utf-8", "replace")
            for href, label in re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
                label = re.sub(r"<[^>]+>", "", label)
                if "招待" in label and "競泳" in label and "結果" in label:
                    cands.append(html.unescape(href))
    for url in dict.fromkeys(cands):
        st, body, _, _ = fetch(url)
        if st != 200:
            continue
        text = body.decode("utf-8", "replace")
        links = re.findall(r'href="(https?://tokyo-swim\.org/result/2026/[^"]+\.pdf)"', text)
        if links:
            state["post"] = url
            found = {}
            for l in links:
                m = re.search(r"/(S?)(\d{3})\.pdf$", l)
                if m:
                    found[("start" if m.group(1) else "result", int(m.group(2)))] = l
            log.append("結果ページ %s から %d件のPDFリンク" % (url, len(found)))
            return found
    return None


def match_rows(kind, rows, sw_by_no, sw_index, sw_by_name, problems, label):
    for r in rows:
        r["sw"] = None
        if kind == "start" and r.get("regno") in sw_by_no:
            s = sw_by_no[r["regno"]]
            if key(s[1]) == key(r["name"]):
                r["sw"] = s[0]
                continue
            problems.append("%s 登録No.%s の氏名が選手番号一覧と違う（%s / %s）" % (label, r["regno"], r["name"], s[1]))
        hit = sw_index.get((key(r["name"]), key(r["team"])), [])
        if len(hit) != 1:
            hit = sw_by_name.get(key(r["name"]), [])
        if len(hit) == 1:
            r["sw"] = hit[0]
        else:
            problems.append("%s 照合できない: %s（%s）候補%d" % (label, r["name"], r["team"], len(hit)))


def same_event(head, p):
    want_round = {"予選": "予選", "タイム決勝": "タイム決勝", "決勝": "決勝", "区分決勝": "決勝"}[p["round"]]
    if (head["no"], head["gender"], head["distance"], key(head["stroke"]), head["round"]) != \
            (p["no"], p["gender"], p["distance"], key(p["stroke"]), want_round):
        return False
    if p["round"] == "区分決勝" and digits(head["cat"]) != digits(p["cat"]):
        return False
    if p.get("from") and head.get("from") and head["from"] != p["from"]:
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="JSTの時刻を上書き（試験用） 例 2026-10-30T15:00")
    ap.add_argument("--base", help="PDFの置き場所を直接指定（記事を探さない）")
    ap.add_argument("--out", help="live.json の書き出し先（試験用）")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()

    now = datetime.datetime.fromisoformat(args.now).replace(tzinfo=JST) if args.now else datetime.datetime.now(JST)
    base = json.loads((ROOT / "data" / "base.json").read_text(encoding="utf-8"))
    live_path = pathlib.Path(args.out) if args.out else ROOT / "data" / "live.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.exists() else {"programs": {}, "state": {}}
    state = live.setdefault("state", {})
    progs = {p["no"]: p for p in base["program"]}
    log, problems = [], []

    if now.date() < MEET_DATES[0] and not args.now:
        print("大会前（%s）。何もしない" % now.strftime("%m/%d %H:%M"))
        return
    today = sum(1 for d in MEET_DATES if d <= now.date())   # 1〜5、大会後は5

    def mins(t):
        h, m = map(int, t.split(":"))
        return h * 60 + m

    # 結果が出るのはそのレースが終わってから＝同じ日の次の競技No.の開始時刻ごろ。その2分前から見に行く。
    # ただし次が休憩をはさむ決勝のとき（予選の最後のレース）は、二次要項の「予選終了予定」までとする。
    # 日の最後のレースは「決勝終了予定」。この2つは開始から5分より前にはしない。
    sched = base["meet"]["daySchedule"]
    end_of = {}
    for p in base["program"]:
        nxt = progs.get(p["no"] + 1)
        sch = sched[p["day"] - 1]
        if not nxt or nxt["day"] != p["day"]:
            end = max(mins(sch[4]), mins(p["start"]) + 5)
        elif p["round"] == "予選" and nxt["round"] != "予選":
            end = max(mins(sch[2]), mins(p["start"]) + 5)
        else:
            end = mins(nxt["start"])
        end_of[p["no"]] = end

    def ended(p):
        if p["day"] != today:
            return p["day"] < today
        return now.hour * 60 + now.minute >= end_of[p["no"]] - 2

    links = None if args.base else discover(state, log)
    want = []
    for p in base["program"]:
        if p["round"] in ("決勝", "区分決勝") and ended(progs[p["from"]]):
            want.append(("start", p["no"]))
        if ended(p):
            want.append(("result", p["no"]))
    targets = []
    for kind, no in want:
        if links is not None:
            url = links.get((kind, no))
            if not url:
                continue
        else:
            url = (args.base or GUESS_BASE) + ("S" if kind == "start" else "") + "%03d.pdf" % no
        rec = live["programs"].get(str(no), {}).get(kind)
        # 取り込み済みのPDFは訂正だけ確かめればよいので間を空ける（当日5分・前日まで30分）。
        # まだ出ていないPDFは毎回見に行く。
        if rec:
            gap = 5 if progs[no]["day"] == today else 30
            if rec.get("checked", "") > (now - datetime.timedelta(minutes=gap)).isoformat():
                continue
        targets.append((kind, no, url, rec))

    sw_by_no = {s[0]: s for s in base["swimmers"]}
    team_name = {t["id"]: t["name"] for t in base["teams"]}
    sw_index, sw_by_name = {}, {}
    for s in base["swimmers"]:
        sw_index.setdefault((key(s[1]), key(team_name[s[2]])), []).append(s[0])
        sw_by_name.setdefault(key(s[1]), []).append(s[0])

    def job(t):
        kind, no, url, rec = t
        return t, fetch(url, (rec or {}).get("etag") if rec and rec.get("url") == url else None)

    changed = 0
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        for (kind, no, url, rec), (st, blob, etag, lm) in ex.map(job, targets):
            p = progs[no]
            label = "No.%d %s" % (no, "スタートリスト" if kind == "start" else "結果")
            if st == 304:
                rec["checked"] = now.isoformat()
                continue
            if st == 0:
                problems.append("%s 通信エラー（前の内容を残す）: %s" % (label, blob.decode(errors="replace")))
                continue
            if st != 200:
                continue
            try:
                parsed = (seiko.parse_start if kind == "start" else seiko.parse_result)(blob)
            except Exception as e:  # 読めないPDFは前の内容を残す
                problems.append("%s を読めない: %s" % (label, e))
                continue
            if not same_event(parsed["head"], p):
                problems.append("%s の見出しが競技日程と違う: %s" % (label, parsed["head"]))
                continue
            rows = parsed["rows"]
            new = {"url": url, "etag": etag, "modified": lm, "gen": parsed["gen"], "checked": now.isoformat(),
                   "drawn": parsed.get("drawn"), "rows": rows,
                   "sha": hashlib.sha1(blob).hexdigest()[:12]}
            if rec and rec.get("sha") == new["sha"]:
                rec.update(checked=new["checked"], etag=etag)
                continue
            live["programs"].setdefault(str(no), {})[kind] = new
            changed += 1
            log.append("%s %d行 取り込み" % (label, len(rows)))

    # 照合は毎回全部やり直す（選手番号一覧を差し替えたときも結果に反映されるように）
    for no_s, rec in live["programs"].items():
        for kind in ("start", "result"):
            if kind in rec:
                label = "No.%s %s" % (no_s, "スタートリスト" if kind == "start" else "結果")
                match_rows(kind, rec[kind]["rows"], sw_by_no, sw_index, sw_by_name, problems, label)

    # 予選・タイム決勝の人数を公式の組数で検算
    warns = []
    for no_s, rec in live["programs"].items():
        p = progs[int(no_s)]
        res = rec.get("result")
        if res and p["round"] in ("予選", "タイム決勝"):
            n = len(res["rows"])
            if math.ceil(n / 10) != p["heats"]:
                warns.append("No.%d %s%s%s 予選: %d人 → %d組になるはずが公式は%d組" % (
                    p["no"], p["gender"], p["distance"], p["stroke"], n, math.ceil(n / 10), p["heats"]))
            heats = {r["heat"] for r in res["rows"]}
            if heats and max(heats) != p["heats"]:
                warns.append("No.%d 結果の組は最大%d、公式は%d組" % (p["no"], max(heats), p["heats"]))
    live["warnings"] = warns
    # 通信エラー・見出し違い・読めないPDFを先に、名前の食い違いは後ろに（数が多くなりがちで前者が埋もれるため）
    serious = lambda x: any(k in x for k in ("通信エラー", "見出し", "読めない"))
    uniq = sorted(set(problems))
    live["problems"] = ([x for x in uniq if serious(x)] + [x for x in uniq if not serious(x)])[:200]
    live["updated"] = now.isoformat(timespec="seconds")

    body = json.dumps(live, ensure_ascii=False, separators=(",", ":"))
    live_path.write_text(body, encoding="utf-8")
    for l in log:
        print("  " + l)
    for w in warns + [x for x in live["problems"] if serious(x)]:
        print("  ! " + w)
    nm = sum(1 for x in set(problems) if not serious(x))
    if nm:
        print("  ! 選手番号一覧と名前・所属が合わない行 %d件（live.json の problems）" % nm)
    print("取り込み %d件 / 確認 %d件 / 取り込み済みの種目 %d" % (changed, len(targets), len(live["programs"])))
    if changed and not args.no_build and not args.out:
        import build
        build.main()


if __name__ == "__main__":
    main()
