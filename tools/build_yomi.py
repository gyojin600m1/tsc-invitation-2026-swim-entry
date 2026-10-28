#!/usr/bin/env python3
"""KANJIDIC2 から、選手名・団体名に出てくる漢字だけの読み表 data/yomi.json を作る。

  python3 tools/build_yomi.py [src/kanjidic2.xml.gz]

読みは検索の照合だけに使い、画面には出さない。
形式 {漢字: [[音・訓…], [名乗りだけの読み…]]}。画面は音訓で当たった人を先に並べる
（「よしだ」で吉田を新田より上に出すため。新の「よし」は名乗り）。
名乗り(nanori)は1文字のもの（「あ」「ま」など）を除く。誤ヒットが増えるため。
"""
import gzip, json, pathlib, sys, xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
XML = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src" / "kanjidic2.xml.gz"
base = json.loads((ROOT / "data" / "base.json").read_text(encoding="utf-8"))

need = set()
for _, name, _ in base["swimmers"]:
    need |= set(name)
for t in base["teams"]:
    need |= set(t["name"])
need = {c for c in need if "一" <= c <= "鿿"}
# 異体字は KANJIDIC2 に読みが無いことがあるので、標準字体の読みを借りる
ALIAS = {"髙": "高", "﨑": "崎", "濵": "濱", "邉": "辺", "邊": "辺", "栁": "柳", "桒": "桑", "眞": "真", "德": "徳",
         "靑": "青", "澤": "沢", "齋": "斎", "齊": "斉", "嶋": "島", "塚": "塚", "瀨": "瀬", "曻": "昇", "隆": "隆",
         "琢": "琢", "禎": "禎", "祐": "祐", "晴": "晴", "淸": "清", "冨": "富", "槇": "槙"}
need |= {v for k, v in ALIAS.items() if k in need}


def k2h(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


VOICE = {"か": "が", "き": "ぎ", "く": "ぐ", "け": "げ", "こ": "ご", "さ": "ざ", "し": "じ", "す": "ず", "せ": "ぜ", "そ": "ぞ",
         "た": "だ", "ち": "ぢ", "つ": "づ", "て": "で", "と": "ど", "は": ["ば", "ぱ"], "ひ": ["び", "ぴ"], "ふ": ["ぶ", "ぷ"],
         "へ": ["べ", "ぺ"], "ほ": ["ぼ", "ぽ"]}


def variants(r):
    out = {r}
    if r[0] in VOICE:
        v = VOICE[r[0]]
        for x in ([v] if isinstance(v, str) else v):
            out.add(x + r[1:])
    if len(r) > 1 and r[-1] in "くきちつ":
        out.add(r[:-1] + "っ")
    if "ず" in r: out.add(r.replace("ず", "づ"))
    if "づ" in r: out.add(r.replace("づ", "ず"))
    if "じ" in r: out.add(r.replace("じ", "ぢ"))
    return out


yomi = {}
with gzip.open(XML) as f:
    for _, el in ET.iterparse(f, events=("end",)):
        if el.tag != "character":
            continue
        lit = el.findtext("literal")
        if lit not in need:
            el.clear()
            continue
        rs, ns = set(), set()
        for rm in el.iter("reading"):
            v = (rm.text or "").strip()
            if not v:
                continue
            if rm.get("r_type") == "ja_on":
                rs.add(k2h(v.replace("-", "").replace(".", "")))
            elif rm.get("r_type") == "ja_kun":
                parts = v.replace("-", "").split(".")
                rs.add("".join(parts))
                rs.add(parts[0])
        for nr in el.iter("nanori"):
            v = (nr.text or "").strip()
            if len(v) >= 2:
                ns.add(v)

        def expand(src):
            out = set()
            for r in src:
                r = "".join(ch for ch in r if "ぁ" <= ch <= "ゖ")
                if r:
                    out |= variants(r)
            return out
        main_r = expand(rs)
        name_r = expand(ns) - main_r
        if main_r or name_r:
            order = lambda xs: sorted(xs, key=lambda x: (-len(x), x))
            yomi[lit] = [order(main_r), order(name_r)]
        el.clear()

for k, v in ALIAS.items():
    if k not in yomi and v in yomi:
        yomi[k] = yomi[v]
miss = sorted(need - set(yomi))
out = ROOT / "data" / "yomi.json"
out.write_text(json.dumps(yomi, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print("漢字 %d字 / 読みあり %d / 読みなし %d %s" % (len(need), len(yomi), len(miss), "".join(miss[:40])))
print("✓ %s (%.0f KB)" % (out.relative_to(ROOT), out.stat().st_size / 1024))
