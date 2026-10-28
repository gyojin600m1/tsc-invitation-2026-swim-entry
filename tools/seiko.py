"""SEIKO Swimming Results System が出す PDF（種目別競技結果・スタートリスト）を座標で読む。

東京都水泳協会が公開する第56回(2025)のPDFで列位置を確かめた。
  結果:       順位@34 組/水路@63 加盟@101 氏名@140 所属@207 生年@303 学年@351 記録@437〜 反応(@514〜
  スタートリスト: 組@36 水路@64(補欠@50) 加盟@83 登録No@144 氏名@177 ヨミガナ@260 所属名@330 学年@448 ｴﾝﾄﾘｰﾀｲﾑ@481
"""
import io, logging, re, unicodedata
import pdfplumber

logging.disable(logging.WARNING)
HEAD = re.compile(r"競技No\.\s*:\s*(\d+)\s+(男子|女子)\s+(\d+m)\s+(\S+)\s+(予選|決勝|タイム決勝)\s*$")
TIME = re.compile(r"^(?:\d+:)?\d{1,2}\.\d{2}$")
NOTES = ("棄権", "失格", "記録なし", "途中棄権", "DNS", "DSQ", "DQ")


def _nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def _lines(page):
    out = {}
    for w in page.extract_words():
        out.setdefault(round(w["top"]), []).append(w)
    return {t: sorted(ws, key=lambda w: w["x0"]) for t, ws in out.items()}


def _header(words_by_line, top_lo, top_hi):
    """「競技No. : 17 女子 100m 自由形 決勝」と次の行の区分（全年齢 / 10～11歳）を取る。"""
    head = None
    for t in sorted(words_by_line):
        if not (top_lo <= t <= top_hi):
            continue
        txt = _nfkc(" ".join(w["text"] for w in words_by_line[t]))
        if head is None:
            m = HEAD.search(txt)
            if m:
                head = {"no": int(m.group(1)), "gender": m.group(2), "distance": m.group(3),
                        "stroke": m.group(4), "round": m.group(5), "cat": None, "from": None}
                continue
        elif head["cat"] is None:
            m = re.search(r"予選No\.\s*:\s*(\d+)", txt)
            if m:
                head["from"] = int(m.group(1))
                txt = txt[m.end():]
            head["cat"] = txt.replace(" ", "").replace("~", "～") or "全年齢"
            break
    return head


def _gen_time(words_by_line):
    for t in sorted(words_by_line, reverse=True):
        txt = " ".join(w["text"] for w in words_by_line[t])
        m = re.search(r"(\d{4}/\d{2}/\d{2}) (\d{2}:\d{2}:\d{2})", txt)
        if m:
            return m.group(1) + " " + m.group(2)
    return ""


def parse_result(blob):
    """種目別競技結果。全ページで同じ競技No.のはず。行: dict(rank, heat, lane, pref, name, team, yob, grade, time, note, rt)"""
    head, rows, gen = None, [], ""
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        for page in pdf.pages:
            L = _lines(page)
            h = _header(L, 120, 150)
            if h is None:
                continue
            if head is None:
                head = h
            elif h["no"] != head["no"]:
                raise ValueError("1つのPDFに別の競技No.が混ざっている: %s %s" % (head["no"], h["no"]))
            gen = _gen_time(L) or gen
            words = [w for ws in L.values() for w in ws]
            anchors = sorted((w for w in words if 50 <= w["x0"] < 100 and w["top"] > 175
                              and re.fullmatch(r"\d+/\d+", w["text"])), key=lambda w: w["top"])
            for a in anchors:
                y = a["top"]
                row = [w for w in words if abs(w["top"] - y) < 2]
                val = lambda lo, hi: " ".join(w["text"] for w in row if lo <= w["x0"] < hi)
                # 記録・反応時間・棄権は同じ行か、1pt下にずれて出ることがある。ラップは約9pt下なので拾わない。
                block = [w for w in words if y - 2 <= w["top"] < y + 5 and 420 <= w["x0"] < 600]
                times = [w["text"] for w in block if TIME.fullmatch(w["text"]) and w["x0"] < 512]
                notes = [w["text"] for w in block if any(n in w["text"] for n in NOTES)]
                rt = "".join(w["text"] for w in block if w["x0"] >= 512).strip("()")
                heat, lane = a["text"].split("/")
                rank = val(0, 50)
                rows.append({
                    "rank": int(rank) if rank.isdigit() else None,
                    "heat": int(heat), "lane": int(lane),
                    "pref": val(100, 139).replace(" ", ""),
                    "name": val(139, 206), "team": val(206, 302),
                    "yob": val(302, 350), "grade": val(350, 420),
                    "time": times[0] if times else "",
                    "note": " ".join(dict.fromkeys(notes)),
                    "rt": rt if re.fullmatch(r"-?\d+\.\d+", rt or "") else "",
                })
                if len(times) > 1:
                    raise ValueError("記録が2つある: %s %s" % (rows[-1]["name"], times))
                if not times and not notes:
                    raise ValueError("記録も棄権等も無い行: %s" % rows[-1]["name"])
    if head is None:
        raise ValueError("見出し（競技No.）が読めない")
    return {"head": head, "gen": gen, "rows": rows}


def parse_start(blob):
    """スタートリスト。行: dict(heat, lane(None=補欠), reserve, pref, regno, name, kana, team, grade, entry)"""
    head, rows, gen, drawn = None, [], "", False
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        for page in pdf.pages:
            L = _lines(page)
            h = _header(L, 110, 185)
            if h is None:
                continue
            if head is None:
                head = h
            elif h["no"] != head["no"]:
                raise ValueError("1つのPDFに別の競技No.が混ざっている")
            gen = _gen_time(L) or gen
            drawn = drawn or any("抽選済" in w["text"] for ws in L.values() for w in ws)
            # 「水路 加盟 登録No …」の見出し行より下が本体（抽選済みの印があると全体が約18pt下がる）
            label = min((t for t, ws in L.items() if any(w["text"] == "登録No" for w in ws)), default=None)
            if label is None:
                continue
            heat = None
            for t in sorted(L):
                if t <= label + 4:
                    continue
                ws = L[t]
                first = ws[0]
                m = re.fullmatch(r"(\d+)組", first["text"])
                if m and first["x0"] < 60:
                    heat = int(m.group(1))
                    continue
                val = lambda lo, hi: " ".join(w["text"] for w in ws if lo <= w["x0"] < hi)
                lane_txt = val(45, 80)
                reserve = lane_txt == "補欠"
                if not (reserve or lane_txt.isdigit()):
                    continue
                regno = val(130, 176)
                if not regno.isdigit():
                    continue
                # 学年とｴﾝﾄﾘｰﾀｲﾑは右寄せで位置が揺れる（1:04.89 は x=472、57.81 は x=481）ので形で分ける
                tail = [w["text"] for w in ws if w["x0"] >= 446]
                rows.append({
                    "heat": heat, "lane": None if reserve else int(lane_txt), "reserve": reserve,
                    "pref": val(80, 130).replace(" ", ""), "regno": int(regno),
                    "name": val(176, 258), "kana": val(258, 329), "team": val(329, 446),
                    "grade": " ".join(x for x in tail if not TIME.fullmatch(x)),
                    "entry": " ".join(x for x in tail if TIME.fullmatch(x)),
                })
    if head is None:
        raise ValueError("見出し（競技No.）が読めない")
    return {"head": head, "gen": gen, "drawn": drawn, "rows": rows}
