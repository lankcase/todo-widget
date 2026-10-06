"""通过 GitHub Git Data API 推送当前仓库内容（用于 github.com:443 被阻断的环境）。

关键点：完全复现本地 HEAD 提交的 tree/author/committer/message，
（不传 date 就无法复现，所以这几个字段都要显式带上）
这样远端提交的 SHA 与本地一致，两边历史不会分叉，以后网络恢复直接 git push 即可。
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "lankcase/todo-widget"


def run(args: list[str], data: str | None = None) -> str:
    p = subprocess.run(args, input=data, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} 失败: {p.stderr.strip()[:400]}")
    return p.stdout


def run_bytes(args: list[str]) -> bytes:
    p = subprocess.run(args, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} 失败: {p.stderr.decode('utf-8', 'replace')[:400]}")
    return p.stdout


def api(method: str, path: str, payload: dict | None = None) -> dict:
    args = ["gh", "api", "--method", method, f"repos/{REPO}/{path}"]
    if payload is not None:
        args += ["--input", "-"]
        out = run(args, json.dumps(payload, ensure_ascii=False))
    else:
        out = run(args)
    return json.loads(out) if out.strip() else {}


def main() -> int:
    root = Path(".").resolve()
    local_sha = run(["git", "rev-parse", "HEAD"]).strip()
    raw = run(["git", "cat-file", "commit", local_sha])

    # 解析本地提交对象的原始头部
    head, _, message = raw.partition("\n\n")
    fields: dict[str, str] = {}
    parents: list[str] = []
    for line in head.splitlines():
        key, _, value = line.partition(" ")
        if key == "parent":
            parents.append(value)
        elif key in ("tree", "author", "committer"):
            fields.setdefault(key, value)

    def parse_ident(value: str) -> dict:
        m = re.match(r"^(.*?)\s*<([^>]*)>\s*(\d+)\s*([+-])(\d{2})(\d{2})$", value)
        if not m:
            raise RuntimeError(f"无法解析身份行: {value}")
        name, email, ts, sign, hh, mm = m.groups()
        # GitHub 要 ISO 8601，时区偏移必须带冒号：+0800 -> +08:00
        offset = f"{sign}{hh}:{mm}"
        import datetime
        seconds = int(hh) * 3600 + int(mm) * 60
        if sign == "-":
            seconds = -seconds
        dt = (datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
              + datetime.timedelta(seconds=seconds))
        return {"name": name, "email": email,
                "date": dt.strftime("%Y-%m-%dT%H:%M:%S") + offset}

    author = parse_ident(fields["author"])
    committer = parse_ident(fields["committer"])
    print(f"本地提交 {local_sha[:12]}（父提交 {len(parents)} 个）")
    print(f"  author  = {author['name']} <{author['email']}> {author['date']}")
    print(f"  message = {message.splitlines()[0][:50]}…")

    # 用 -z + core.quotePath=false 读取文件名：默认会把中文路径转义成 \344 这样的八进制
    listing = run(["git", "-c", "core.quotePath=false", "ls-files", "-z"])
    files = [f for f in listing.split("\0") if f.strip()]
    print(f"\n共 {len(files)} 个文件，开始上传 blob…")

    entries = []
    for i, f in enumerate(files, 1):
        # 必须从 git 对象库取内容，而不是读工作区文件：
        # git 提交时按 core.autocrlf 做过换行规范化，直接读磁盘会得到不同的字节，
        # blob 的 SHA 就对不上了（第一次就是栽在这里）。
        content = run_bytes(["git", "cat-file", "blob", f"HEAD:{f}"])
        blob = api("POST", "git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({"path": f.replace("\\", "/"), "mode": "100644",
                        "type": "blob", "sha": blob["sha"]})
        print(f"  [{i}/{len(files)}] {f}  {len(content)} B")

    print("\n创建 tree…")
    tree = api("POST", "git/trees", {"tree": entries})
    print(f"  tree sha = {tree['sha']}")

    # 与本地 root tree 对比，确保内容完全一致
    local_tree = fields["tree"]
    if tree["sha"] == local_tree:
        print("  ✓ 与本地 tree 完全一致")
    else:
        print(f"  ! 与本地 tree 不一致（本地 {local_tree}）")

    print("\n创建 commit…")
    # parents 必须一起带上：漏掉它，非首次提交算出来的 SHA 就和本地不一样
    payload: dict = {"message": message, "tree": tree["sha"],
                     "author": author, "committer": committer}
    if parents:
        payload["parents"] = parents
    commit = api("POST", "git/commits", payload)
    print(f"  commit sha = {commit['sha']}")
    if commit["sha"] == local_sha:
        print("  ✓ 提交哈希与本地一致，两边完全同步")
    else:
        print(f"  ! 提交哈希与本地不同（本地 {local_sha}）")

    print("\n写入 refs/heads/main …")
    try:
        api("POST", "git/refs", {"ref": "refs/heads/main", "sha": commit["sha"]})
        print("  已创建 main 分支")
    except RuntimeError as exc:
        if "already exists" in str(exc) or "422" in str(exc):
            api("PATCH", "git/refs/heads/main", {"sha": commit["sha"], "force": True})
            print("  已更新 main 分支")
        else:
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
