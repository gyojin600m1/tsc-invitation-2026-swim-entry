#!/usr/bin/env python3
# 第57回東京SC優秀選手招待 2026/10/30-11/3 の結果・決勝スタートリストを公式PDFから取り込み、GitHubへ送る。
# GitHub Actions の cron は数時間おきにしか動かないことがあった（日本選手権25mのとき）ので、この Mac の launchd で毎分動かす。
# 走る時間帯：10/30〜11/3 の 8:50〜19:00（JST）。11/3 19:30 を過ぎたら自分を止める。
#
# 試験用: 環境変数 TSC_NOW=2026-10-30T16:00（時刻を上書きして live_update.py にも渡す）・
#         TSC_BASE=https://tokyo-swim.org/result/2025/251030/（昨年のPDFで試す）。TSC_NOW があるときは通知を出さない。
import datetime, os, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(HERE, "log.txt")
FAILED = os.path.join(HERE, ".push_failed")   # 送信失敗の通知を1回だけ出すための印
PY = "/Library/Developer/CommandLineTools/usr/bin/python3"
GIT = "/Library/Developer/CommandLineTools/usr/bin/git"
LABEL = "com.fukuda.swim.tsc-invitation-2026"
DAYS = {"2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"}
STOP_AFTER = "2026-11-03 19:30"
FILES = ["data/live.json", "index.html", "version.json"]


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%m/%d %H:%M ") + " ".join(msg.split()) + "\n")   # git のエラーは複数行なので1行にまとめる


def notify(title, msg):
    if os.environ.get("TSC_NOW"):
        return
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'], check=False)


def stop_myself():
    log("期間終了。見張りを止めます")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
    raise SystemExit(0)


def git(*args):
    return subprocess.run([GIT, *args], cwd=ROOT, capture_output=True, text=True)


def run_import():
    """tools/live_update.py を動かす。(成功したか, 最後の行, 何か取り込んだか)"""
    args = [PY, os.path.join(ROOT, "tools", "live_update.py")]
    if os.environ.get("TSC_NOW"):
        args += ["--now", os.environ["TSC_NOW"]]
    if os.environ.get("TSC_BASE"):
        args += ["--base", os.environ["TSC_BASE"]]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return False, ((r.stderr or "").strip().splitlines() or [""])[-1][:200], False
    lines = (r.stdout or "").strip().splitlines()
    # 取り込みがあると最後に build.py の行が続くので、「取り込み N件 …」の要約行を探す
    summary = next((l for l in reversed(lines) if l.startswith("取り込み ")), lines[-1] if lines else "")
    return True, summary, summary.startswith("取り込み ") and not summary.startswith("取り込み 0件")


def commit_results():
    git("add", *FILES)
    if not git("status", "--porcelain", *FILES).stdout.strip():
        return
    git("-c", "user.name=gyojin600m1", "-c", "user.email=gyojin600m1@gmail.com", "commit", "-q", "-m", "速報を更新")


def unpushed():
    r = git("rev-list", "--count", "@{u}..HEAD")
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def publish():
    """まだ GitHub に無いコミットを送る。(送れたか, 説明)"""
    p = git("push", "-q", "origin", "main")
    if p.returncode == 0:
        return True, ""
    # GitHub 側が先に進んでいた（スマホからの手動取り込みなど）。rebase で片方のファイルを丸ごと選ぶと、
    # data/live.json は1行なので、こちらで取り込んだ結果がまるごと消えることがある。
    # そこで GitHub の内容を土台にして取り込み直し、両方の結果がそろった状態で送る。
    if git("fetch", "-q", "origin").returncode != 0:
        return False, "GitHub から取得できない: " + (p.stderr or "").strip()[:120]
    if int(git("rev-list", "--count", "HEAD..origin/main").stdout.strip() or 0) == 0:
        # GitHub は先に進んでいない＝通信や認証で送れなかっただけ。コミットは残して次の回に送り直す
        return False, (p.stderr or "").strip()[:150]
    git("reset", "-q", "--mixed", "origin/main")       # 履歴だけ GitHub に合わせる（ほかの作業中ファイルは触らない）
    if git("cat-file", "-e", "origin/main:data/live.json").returncode == 0:
        for f in FILES:
            git("checkout", "origin/main", "--", f)      # 結果のファイルは GitHub のものから始める
        ok, last, changed = run_import()                 # GitHub に無い分だけ取り込み直される
        if not ok:
            return False, "取り込み直しに失敗: " + last
        if changed:
            commit_results()
    else:
        commit_results()                                 # GitHub にまだ結果が無い。こちらの結果をそのまま積む
    if unpushed() == 0:
        return True, "GitHub 側がすでに最新"
    p = git("push", "-q", "origin", "main")
    return p.returncode == 0, (p.stderr or "").strip()[:150]


def main():
    now = datetime.datetime.fromisoformat(os.environ["TSC_NOW"]) if os.environ.get("TSC_NOW") else datetime.datetime.now()
    # 自分を止める判定は時間帯のガードより先に置く（窓の外だと止まれなくなるため）
    if now.strftime("%Y-%m-%d %H:%M") > STOP_AFTER:
        stop_myself()
    if now.strftime("%Y-%m-%d") not in DAYS:
        return
    if not (8 * 60 + 50 <= now.hour * 60 + now.minute <= 19 * 60):
        return

    ok, last, changed = run_import()
    if not ok:
        log("取り込み失敗: " + last)
    elif changed:
        commit_results()
    # 新しいPDFが無い回でも、前に送れなかったコミットが残っていれば送り直す
    pending = unpushed()
    if not pending:
        return
    sent, why = publish()
    if sent:
        log((last if ok else "前回の分") + "（GitHub へ送信" + ("・" + why if why else "") + "）")
        if os.path.exists(FAILED):
            os.remove(FAILED)
    else:
        log("送信失敗（次の回にまた送る）: " + why)
        if not os.path.exists(FAILED):
            open(FAILED, "w").close()
            notify("東京SC招待 速報", "GitHubへ送れませんでした。次の回にまた送ります")


if __name__ == "__main__":
    main()
