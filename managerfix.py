#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""comfyui-manager-fix —— 让 ComfyUI-Manager 在连不上 raw.githubusercontent.com 的网络里能用。

背景：Manager 的节点清单默认从 raw.githubusercontent.com 拉。
那个域名在很多网络里是连不上的，于是 Manager 打开后一片空白，
报 "Failed to get external model list"。

这个工具做三件事：
    诊断   你的 Manager 是哪一版、哪些地址没走镜像、镜像能不能连上
    修复   把清单地址改成一个可达的镜像
    校验   真去拉一次清单，拉到了才算数

用法：
    python managerfix.py --check          只诊断，一个字节都不改
    python managerfix.py --fix            修（幂等，可反复跑）
    python managerfix.py --verify         只验连通性

    --instance <路径>   指定 ComfyUI 目录（不给就自动找）
    --mirror <前缀>     换一个镜像前缀（默认 https://ghfast.top/）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_MIRROR = "https://ghfast.top/"
UPSTREAM_BASES = [
    "https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main",
    "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main",
]
CHANNEL_FILES = ("custom-node-list.json", "model-list.json")

BAK_SUFFIX = ".bak_managerfix"


# ---------------------------------------------------------------- 找 ComfyUI

def candidate_roots():
    """能想到的 ComfyUI 目录都列出来，按可能性排序。"""
    roots = []

    env = os.environ.get("COMFYUI_HOME")
    if env:
        roots.append(Path(env))

    # 桌面版：安装位置记录在 %APPDATA%\Comfy Desktop\installations.json
    appdata = os.environ.get("APPDATA")
    if appdata:
        inst = Path(appdata) / "Comfy Desktop" / "installations.json"
        if inst.is_file():
            try:
                data = json.loads(inst.read_text(encoding="utf-8"))
                for v in _walk_strings(data):
                    p = Path(v)
                    if p.name.lower() == "comfyui" and p.is_dir():
                        roots.append(p)
            except Exception:
                pass

    home = Path.home()
    for name in ("ComfyUI", "ComfyUI_windows_portable", "comfyui", "ComfyUI-Installs"):
        roots.append(home / name)
    for drive in ("C:", "D:", "E:", "F:"):
        roots.append(Path(drive + os.sep) / "ComfyUI")
        roots.append(Path(drive + os.sep) / "ComfyUI_windows_portable")

    # 去重保序
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def looks_like_comfyui(p: Path):
    return (p / "custom_nodes").is_dir() or (p / "main.py").is_file()


def resolve_comfyui(p: Path):
    """p 本身是 ComfyUI 目录，或者 p\\ComfyUI 是，都认。"""
    if looks_like_comfyui(p):
        return p
    sub = p / "ComfyUI"
    try:
        if sub.is_dir() and looks_like_comfyui(sub):
            return sub
    except Exception:
        pass
    return None


def find_instance(given: str):
    """返回 (ComfyUI 目录, 说明)。找不到返回 (None, 原因)。"""
    if given:
        p = Path(given).expanduser()
        if not p.is_dir():
            return None, f"给的路径不存在：{p}"
        hit = resolve_comfyui(p)
        if hit:
            return hit, "用 --instance 指定的"
        return None, f"{p} 里没有 custom_nodes\\ 或 main.py，看起来不是 ComfyUI 目录"

    for r in candidate_roots():
        try:
            if not r.is_dir():
                continue
            hit = resolve_comfyui(r)
            if hit:
                return hit, "自动找到的"
        except Exception:
            continue
    return None, "没找到 ComfyUI 目录，请用 --instance 指定"


# ---------------------------------------------------------------- 版本

def manager_version(mgr_dir: Path):
    """读 Manager 的版本号，读不到返回空串。"""
    for name in ("pyproject.toml", "setup.py"):
        p = mgr_dir / name
        if not p.is_file():
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r'version\s*=\s*["\']([0-9][^"\']*)["\']', t)
        if m:
            return m.group(1)
    p = mgr_dir / "glob" / "manager_core.py"
    if p.is_file():
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'version\s*=\s*["\']([0-9][^"\']*)["\']', t)
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""


# ---------------------------------------------------------------- 检查

class Report:
    def __init__(self):
        self.items = []

    def add(self, ok, title, detail=""):
        self.items.append({"ok": ok, "title": title, "detail": detail})

    @property
    def failed(self):
        return [i for i in self.items if not i["ok"]]

    def show(self):
        for i in self.items:
            mark = "[OK]  " if i["ok"] else "[FAIL]"
            print(f"  {mark} {i['title']}")
            if i["detail"]:
                for line in i["detail"].splitlines():
                    print(f"         {line}")


def read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def count_plain_urls(text, bases):
    """统计文件里还有多少处没走镜像的原始地址。"""
    n = 0
    for base in bases:
        pat = re.compile(r"(?<!https://ghfast\.top/)" + re.escape(base))
        n += len(pat.findall(text))
    return n


def check(inst: Path, mgr: Path, user_dir: Path, mirror: str):
    r = Report()

    # 1. Manager 装没装
    installed = mgr.is_dir()
    r.add(installed, "ComfyUI-Manager 已安装", str(mgr) if installed else f"找不到 {mgr}")
    if not installed:
        return r

    ver = manager_version(mgr)
    r.add(True, f"Manager 版本：{ver or '(读不到版本号)'}",
          "这个工具针对 custom_nodes 里装的 Manager 3.x。\n"
          "如果你是 pip 装的 4.x，多数情况下不需要这个工具。")

    # 2. channels.list
    cl = user_dir / "channels.list"
    if not cl.is_file():
        r.add(False, "channels.list 不存在",
              "Manager 会用它决定从哪拉清单；缺了就会用代码里的默认地址。\n"
              "--fix 会新建一份指向镜像的。")
    else:
        t = read_text(cl) or ""
        plain = count_plain_urls(t, UPSTREAM_BASES)
        mirrored = len(re.findall(r"https://ghfast\.top/", t))
        r.add(plain == 0, f"channels.list：{mirrored} 行走镜像，{plain} 处还没走",
              "" if plain == 0 else "这些地址在你现在的网络里可能拉不到。")

    # 3. config.ini 的 channel_url
    cfg = user_dir / "config.ini"
    if not cfg.is_file():
        r.add(False, "config.ini 不存在", "--fix 会新建一份。")
    else:
        t = read_text(cfg) or ""
        m = re.search(r"^\s*channel_url\s*=\s*(\S+)\s*$", t, re.M)
        if not m:
            r.add(False, "config.ini 里没有 channel_url", "--fix 会补上指向镜像的一行。")
        else:
            url = m.group(1)
            ok = url.startswith(mirror) or "ghfast.top" in url
            r.add(ok, f"channel_url = {url}",
                  "" if ok else "还指向原始地址，需要改成镜像。")

    # 4. 源码里写死的地址
    plain_files = []
    for p in mgr.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".template"):
            continue
        if ".git" in p.parts:
            continue
        t = read_text(p)
        if t and count_plain_urls(t, UPSTREAM_BASES) > 0:
            plain_files.append(str(p.relative_to(mgr)))
    r.add(not plain_files, f"源码里写死的原始地址：{len(plain_files)} 个文件",
          "\n".join(plain_files[:8]) if plain_files else "")

    # 5. 下载器默认镜像
    dl = mgr / "glob" / "manager_downloader.py"
    if dl.is_file():
        t = read_text(dl) or ""
        ok = "or 'https://hf-mirror.com'" in t or "HF_ENDPOINT" not in t
        r.add(ok, "模型下载器默认走镜像",
              "" if ok else "需要 HF_ENDPOINT 环境变量才走镜像；--fix 会让它默认就走。")

    return r


def verify(mirror: str):
    r = Report()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    base = mirror.rstrip("/") + "/" + UPSTREAM_BASES[0]

    for name in CHANNEL_FILES:
        url = base + "/" + name
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "managerfix"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                size = len(resp.read())
            r.add(True, f"{name}  {size / 1024:.0f} KB  {time.time() - t0:.1f}s", url)
        except Exception as e:
            r.add(False, f"{name} 拉取失败", f"{url}\n{str(e)[:120]}")
    return r


# ---------------------------------------------------------------- 修复

def patch_text(text, bases, mirror):
    """把原始地址换成镜像地址。已经包过的不再包，防止叠成两层。"""
    for base in bases:
        pat = re.compile(r"(?<!https://ghfast\.top/)" + re.escape(base))
        text = pat.sub(mirror.rstrip("/") + "/" + base, text)
    # 历史版本可能已经叠过，折叠回一层
    text = re.sub(r"(?:https://ghfast\.top/){2,}", "https://ghfast.top/", text)
    return text


def write_with_backup(path: Path, new_text: str):
    old = read_text(path)
    if old == new_text:
        return False
    if not path.exists() or not Path(str(path) + BAK_SUFFIX).exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            Path(str(path) + BAK_SUFFIX).write_text(old or "", encoding="utf-8")
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return True


def fix(inst: Path, mgr: Path, user_dir: Path, mirror: str):
    r = Report()
    channel = mirror.rstrip("/") + "/" + UPSTREAM_BASES[0]

    if not mgr.is_dir():
        r.add(False, "ComfyUI-Manager 未安装", f"找不到 {mgr}")
        return r

    user_dir.mkdir(parents=True, exist_ok=True)

    # 1. channels.list
    cl = user_dir / "channels.list"
    if cl.is_file():
        old = read_text(cl) or ""
        new = patch_text(old, UPSTREAM_BASES, mirror)
        changed = write_with_backup(cl, new)
        r.add(True, "channels.list " + ("已改为镜像" if changed else "已是镜像，没动"))
    else:
        lines = [f"default::{channel}"]
        for ch in ("recent", "legacy", "forked", "dev", "tutorial"):
            lines.append(f"{ch}::{channel}/node_db/{ch}")
        cl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r.add(True, "channels.list 不存在，已新建")

    # 2. config.ini
    cfg = user_dir / "config.ini"
    old = read_text(cfg) if cfg.is_file() else None
    lines = (old or "[default]").splitlines()
    out, seen = [], False
    for ln in lines:
        if ln.strip().lower().startswith("channel_url"):
            out.append(f"channel_url = {channel}")
            seen = True
        else:
            out.append(ln)
    if not seen:
        if not any(x.strip() == "[default]" for x in out):
            out.insert(0, "[default]")
        out.append(f"channel_url = {channel}")
    changed = write_with_backup(cfg, "\n".join(out).rstrip() + "\n")
    r.add(True, "config.ini channel_url " + ("已指向镜像" if changed else "已是镜像"))

    # 3. 源码里写死的地址
    n = 0
    for p in sorted(mgr.rglob("*")):
        if not p.is_file() or p.suffix not in (".py", ".template"):
            continue
        if ".git" in p.parts or p.name.endswith(BAK_SUFFIX):
            continue
        t = read_text(p)
        if not t:
            continue
        new = patch_text(t, UPSTREAM_BASES, mirror)
        if new != t and write_with_backup(p, new):
            n += 1
    r.add(True, f"源码改了 {n} 个文件" if n else "源码已是镜像，没动")

    # 4. 下载器默认走镜像
    dl = mgr / "glob" / "manager_downloader.py"
    if dl.is_file():
        t = read_text(dl) or ""
        if "or 'https://hf-mirror.com'" in t:
            r.add(True, "下载器已默认走镜像，没动")
        elif "HF_ENDPOINT = os.getenv('HF_ENDPOINT')" in t:
            t2 = t.replace(
                "HF_ENDPOINT = os.getenv('HF_ENDPOINT')",
                "HF_ENDPOINT = os.getenv('HF_ENDPOINT') or 'https://hf-mirror.com'",
            )
            t2 = t2.replace(
                "model_url = model_url.replace('https://huggingface.co', HF_ENDPOINT)",
                "model_url = model_url.replace('https://huggingface.co', HF_ENDPOINT)"
                ".replace('https://hf.co', HF_ENDPOINT)",
            )
            write_with_backup(dl, t2)
            r.add(True, "下载器已改为默认走 hf-mirror")
        else:
            r.add(False, "下载器代码结构变了，找不到替换点",
                  "可能是 Manager 升级换了写法。请把这段报给 issue。")
    return r


# ---------------------------------------------------------------- 入口

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        prog="managerfix.py",
        description="让 ComfyUI-Manager 在连不上 raw.githubusercontent.com 的网络里能用。",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只诊断，不改任何文件")
    mode.add_argument("--fix", action="store_true", help="修复（幂等，可反复跑）")
    mode.add_argument("--verify", action="store_true", help="只验镜像能不能拉到清单")

    ap.add_argument("--instance", default="", help="ComfyUI 目录（不给就自动找）")
    ap.add_argument("--mirror", default=os.environ.get("MANAGERFIX_MIRROR", DEFAULT_MIRROR),
                    help=f"镜像前缀，默认 {DEFAULT_MIRROR}")
    args = ap.parse_args(argv)

    print("=" * 62)
    print("  ComfyUI-Manager 镜像修复")
    print("=" * 62)

    if args.verify:
        report = verify(args.mirror)
        print()
        report.show()
        return _summary(report)

    inst, why = find_instance(args.instance)
    if inst is None:
        print(f"\n  [FAIL] {why}")
        print("         例：--instance E:\\Comfy-Desktop\\ComfyUI-Installs\\ComfyUI\\ComfyUI")
        return 2

    mgr = inst / "custom_nodes" / "ComfyUI-Manager"
    user_dir = inst / "user" / "__manager"
    print(f"  ComfyUI：{inst}  （{why}）")
    print(f"  Manager：{mgr if mgr.is_dir() else '(未安装)'}")
    print(f"  镜像：  {args.mirror}")
    print()

    if args.check:
        report = check(inst, mgr, user_dir, args.mirror)
        report.show()
        print()
        if report.failed:
            print(f"  有 {len(report.failed)} 项要处理。跑 --fix 修，跑 --verify 验连通性。")
        else:
            print("  都正常。如果 Manager 还是空白，跑一次 --verify 看镜像通不通。")
        return _summary(report)

    if args.fix:
        report = fix(inst, mgr, user_dir, args.mirror)
        report.show()
        print()
        print("  接着验一下镜像通不通……")
        vr = verify(args.mirror)
        print()
        vr.show()
        print()
        if vr.failed:
            print("  镜像拉不到。可能是镜像挂了，或你的网络需要代理。")
            print("  重跑一次 --verify 可以只看网络，不动文件。")
        else:
            print("  镜像能拉到清单。现在重启 ComfyUI，Manager 的 Model Manager 就有内容了。")
        return _summary(vr)

    return 0


def _summary(report):
    bad = len(report.failed)
    if bad:
        print(f"\n  {len(report.items) - bad} 项正常，{bad} 项失败")
        return 1
    print(f"\n  {len(report.items)} 项全部正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
