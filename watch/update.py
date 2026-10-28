#!/usr/bin/env python3
# 第57回東京SC優秀選手招待 2026/10/30-11/3 の結果・決勝スタートリストを公式PDFから取り込み、GitHubへ送る。
# GitHub Actions の cron は数時間おきにしか動かないことがあった（日本選手権25mのとき）ので、この Mac の launchd で毎分動かす。
# 走る時間帯：10/30〜11/3 の 8:50〜19:00（JST）。11/3 19:30 を過ぎたら自分を止める。
import datetime, os, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(HERE, "log.txt")
PY = "/Library/Developer/CommandLineTools/usr/bin/python3"
GIT = "/Library/Developer/CommandLineTools/usr/bin/git"
LABEL = "com.fukuda.swim.tsc-invitation-2026"
DAYS = {"2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"}
STOP_AFTER = "2026-11-03 19:30"
FILES = ["data/live.json", "index.html", "version.json"]


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%m/%d %H:%M ") + msg + "\n")


def notify(title, msg):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'], check=False)


def stop_myself():
    log("期間終了。見張りを止めます")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
    raise SystemExit(0)


def main():
    now = datetime.datetime.now()
    # 自分を止める判定は時間帯のガードより先に置く（窓の外だと止まれなくなるため）
    if now.strftime("%Y-%m-%d %H:%M") > STOP_AFTER:
        stop_myself()
    if now.strftime("%Y-%m-%d") not in DAYS:
        return
    if not (8 * 60 + 50 <= now.hour * 60 + now.minute <= 19 * 60):
        return

    r = subprocess.run([PY, os.path.join(ROOT, "tools", "live_update.py")], cwd=ROOT, capture_output=True, text=True)
    last = ((r.stdout or "").strip().splitlines() or [""])[-1]
    if r.returncode != 0:
        log("取り込み失敗: " + ((r.stderr or "").strip().splitlines() or [""])[-1][:200])
        return
    if last.startswith("取り込み 0件"):
        return
    subprocess.run([GIT, "add"] + FILES, cwd=ROOT, check=False)
    st = subprocess.run([GIT, "status", "--porcelain"] + FILES, cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not st:
        return
    subprocess.run([GIT, "-c", "user.name=gyojin600m1", "-c", "user.email=gyojin600m1@gmail.com",
                    "commit", "-q", "-m", "速報を更新"], cwd=ROOT, check=False)
    p = subprocess.run([GIT, "push", "-q", "origin", "main"], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        # 先に他（スマホからの手動実行など）で更新されていたら取り込んでから送り直す
        subprocess.run([GIT, "pull", "-q", "--rebase", "-X", "ours", "origin", "main"], cwd=ROOT, check=False)
        p = subprocess.run([GIT, "push", "-q", "origin", "main"], cwd=ROOT, capture_output=True, text=True)
    if p.returncode == 0:
        log(last)
    else:
        log("送信失敗: " + (p.stderr or "").strip()[:150])
        notify("東京SC招待 速報", "GitHubへ送れませんでした")


if __name__ == "__main__":
    main()
