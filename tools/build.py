#!/usr/bin/env python3
"""data/base.json ＋ data/yomi.json ＋ data/live.json を template.html に差し込んで index.html を作る。

  python3 tools/build.py

書き出す前に件数を公式の集計値と照合する（名簿は一致しないと止める。速報は途中なので表示だけ）。
version.json は画面が「新しい結果があります」を出すための小さな印。
"""
import hashlib, json, math, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name, default=None):
    p = ROOT / "data" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", help="試験用: 別の live.json を使う")
    ap.add_argument("--out", help="試験用: index.html の書き出し先フォルダ")
    args = ap.parse_args(argv if argv is not None else [])
    base = load("base.json")
    yomi = load("yomi.json", {})
    live = json.loads(pathlib.Path(args.live).read_text(encoding="utf-8")) if args.live else load("live.json", {"programs": {}})
    outdir = pathlib.Path(args.out) if args.out else ROOT
    off = base["meet"]["official"]
    progs = {p["no"]: p for p in base["program"]}
    nos = {s[0] for s in base["swimmers"]}

    print("── 件数の照合 ───────────────────────")
    ng = []
    for label, got, want in [("選手", len(base["swimmers"]), off["swimmers"]),
                             ("団体", len(base["teams"]), off["teams"]),
                             ("競技No.", len(base["program"]), off["programs"])]:
        print("   %-8s %6d  %s" % (label, got, "✓" if got == want else "✗ 公式は %d" % want))
        if got != want:
            ng.append(label)

    # 速報を画面用に詰める
    L = {}
    gender, conflict, kana = {}, set(), {}
    for no_s, rec in sorted(live.get("programs", {}).items(), key=lambda x: int(x[0])):
        p = progs[int(no_s)]
        out = {}
        if "result" in rec:
            r = rec["result"]
            rows = []
            for x in r["rows"]:
                sw = x["sw"] if x["sw"] in nos else None
                row = [sw, x["rank"], x["heat"], x["lane"], x["time"], x["note"], x["grade"], x["yob"]]
                if sw is None:
                    row += [x["name"], x["team"]]
                rows.append(row)
            out["r"] = rows
            out["ru"] = r["url"]
            out["rg"] = r["gen"]
        if "start" in rec:
            s = rec["start"]
            rows = []
            for x in s["rows"]:
                sw = x["sw"] if x["sw"] in nos else None
                row = [sw, x["heat"], -1 if x["reserve"] else x["lane"], x["entry"], x["grade"]]
                if sw is None:
                    row += [x["name"], x["team"]]
                rows.append(row)
            out["s"] = rows
            out["su"] = s["url"]
            out["sg"] = s["gen"]
            for x in s["rows"]:
                if x["sw"] in nos and x.get("kana"):
                    kana[x["sw"]] = x["kana"]   # 公式のヨミガナ。検索だけに使い、画面には出さない
        for part in ("r", "s"):
            for row in out.get(part, []):
                if row[0] is not None:
                    g = p["gender"]
                    if gender.get(row[0], g) != g:
                        conflict.add(row[0])
                    gender[row[0]] = g
        L[no_s] = out
    for sw in conflict:
        gender.pop(sw, None)

    # 鹿児島勢 = tools/kagoshima.json（base.home）＋ 結果・スタートリストの加盟が「鹿児島」の選手
    home = {r["no"]: dict(r) for r in base.get("home", [])}
    for rec in live.get("programs", {}).values():
        for kind in ("result", "start"):
            for x in rec.get(kind, {}).get("rows", []):
                if x["sw"] in nos and x.get("pref") == "鹿児島" and x["sw"] not in home:
                    home[x["sw"]] = {"no": x["sw"], "note": "", "origin": False, "live": True}
    for no, r in home.items():
        if r.get("gender") and gender.get(no, r["gender"]) != r["gender"]:
            print("   ! 鹿児島 No.%d の性別が県のデータ（%s）と結果（%s）で違う。結果を使う" % (no, r["gender"], gender[no]))
        elif r.get("gender") and no not in conflict:
            gender.setdefault(no, r["gender"])
    print("   鹿児島勢 %d名（結果で加わった %d名）" % (len(home), sum(1 for r in home.values() if r.get("live"))))

    # 公式の申込数 8,881 との照合（予選・タイム決勝の結果がそろったときに一致するはず）
    entry_progs = [p for p in base["program"] if p["round"] in ("予選", "タイム決勝")]
    have = [p for p in entry_progs if "r" in L.get(str(p["no"]), {})]
    entries = sum(len(L[str(p["no"])]["r"]) for p in have)
    heat_ok = sum(1 for p in have if math.ceil(len(L[str(p["no"])]["r"]) / 10) == p["heats"])
    unmatched = sum(1 for v in L.values() for part in ("r", "s") for row in v.get(part, []) if row[0] is None)
    print("   予選・T決勝の結果  %d / %d 種目（組数一致 %d）" % (len(have), len(entry_progs), heat_ok))
    print("   申込（結果の行数） %d%s" % (entries, "  ✓ 公式 %d" % off["entries"] if len(have) == len(entry_progs) and entries == off["entries"]
                                   else ("  ✗ 公式 %d" % off["entries"] if len(have) == len(entry_progs) else "  （途中）")))
    print("   性別が分かった選手 %d / %d　食い違い %d　照合できない行 %d" % (len(gender), len(nos), len(conflict), unmatched))
    print("────────────────────────────────────")
    if ng:
        sys.exit("✗ 名簿が公式の集計値と合わないので書き出しません")

    ver = hashlib.sha1(json.dumps(L, sort_keys=True).encode()).hexdigest()[:16]
    data = {
        "version": ver,
        "meet": base["meet"], "swimmers": base["swimmers"], "teams": base["teams"],
        "program": base["program"], "invited": base["invited"], "yomi": yomi,
        "live": L, "gender": gender, "conflict": sorted(conflict), "kana": kana,
        "home": [{k: v for k, v in r.items() if k != "gender"} for r in home.values()],
        "homeTeams": base.get("homeTeams", []),
        "updated": live.get("updated", ""),
        "check": {"entryEvents": len(entry_progs), "haveEvents": len(have), "entries": entries,
                  "heatOk": heat_ok, "unmatched": unmatched, "warnings": live.get("warnings", [])},
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    a, b = tpl.split("/*__DATA__*/", 1)
    _, c = b.split("/*__DATA_END__*/", 1)
    html = a + "/*__DATA__*/" + payload + "/*__DATA_END__*/" + c
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "index.html").write_text(html, encoding="utf-8")
    (outdir / "version.json").write_text(json.dumps({"v": ver, "at": data["updated"], "n": len(L)}), encoding="utf-8")
    print("✓ %s (%.0f KB)  version %s" % (outdir / "index.html", (outdir / "index.html").stat().st_size / 1024, ver))


if __name__ == "__main__":
    main(sys.argv[1:])
