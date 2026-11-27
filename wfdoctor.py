#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wfdoctor —— ComfyUI 工作流体检器
用法:
    wfdoctor check <工作流.json> [--host http://127.0.0.1:8188] [--json]
    wfdoctor check-workflows-dir <目录> [--host ...]

做什么:
    静态解析 ComfyUI 工作流（UI 格式 / API 格式 / 含 subgraph 的新版格式），
    对照正在运行的 ComfyUI（/object_info），一次性告出：
      1) 缺哪些自定义节点（及它属于哪个扩展包）
      2) 哪些模型文件本地没有（UNET / LoRA / VAE / CLIP / Checkpoint / upscale …）
      3) 输入线断了（节点输入被引用但没人连）
      4) 必需输入缺 widget 值
      5) 工作流里写死、但本机不存在的文件路径
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 数据模型

MODEL_INPUT_HINTS = (
    "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name",
    "clip_name1", "clip_name2", "clip_name3", "clip_vision_name",
    "control_net_name", "style_model_name", "upscale_model_name",
    "model_name", "sam_model_name", "detector_name", "gligen_name",
    "hypernetwork_name", "photomaker_model_name", "pulid_file",
    "gguf_name", "depth_model", "ckpt_name2",
)

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# 这些是 ComfyUI 前端（litegraph / rgthree / KJNodes 等）自带的虚拟节点，
# 不需要后端安装，也不出现在 /object_info 里。必须白名单，否则报"缺节点"是假阳性。
FRONTEND_ONLY_NODES = {
    # litegraph 内核
    "Reroute", "PrimitiveNode", "Note", "MarkdownNote", "PreviewAny",
    # 全局变量 / 注释类
    "SetNode", "GetNode", "SetNode|pysssss", "GetNode|pysssss",
    "Note Plus (mtb)", "Note (mtb)", "Text Note",
    # rgthree
    "Fast Groups Bypasser (rgthree)", "Fast Groups Muter (rgthree)",
    "Label (rgthree)", "Bookmark (rgthree)", "Node Collector (rgthree)",
    "Fast Bypasser (rgthree)", "Fast Muter (rgthree)",
    "Context (rgthree)", "Context Switch (rgthree)", "Context Big (rgthree)",
    "Seed (rgthree)", "Power Lora Loader (rgthree)", "Any Switch (rgthree)",
    # efficiency-nodes 的 UI-only 节点
    "Anything Everywhere", "Anything Everywhere3", "Anything Everywhere?",
    "Anything EverywhereTriple",
    # 工作台/第三方常用 UI 占位
    "ShowText|pysssss", "easy showAnything", "easy showAnythingLite",
}


@dataclass
class Finding:
    severity: str      # "error" | "warn"
    kind: str          # missing_node | missing_model | broken_link | missing_widget | missing_file
    node_id: Any
    node_type: str
    detail: str
    fix: str = ""


@dataclass
class Report:
    path: str = ""
    format: str = ""       # ui | api | unknown
    node_count: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warns(self):
        return [f for f in self.findings if f.severity == "warn"]

    def ok(self):
        return not self.errors


# ---------------------------------------------------------------- 读取 object_info

def fetch_object_info(host: str) -> dict:
    url = host.rstrip("/") + "/object_info"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------- 节点 -> 扩展包 反查表

_NODE_MAP_CACHE: dict[str, str] | None = None


def load_node_map() -> dict[str, str]:
    """加载 节点类型 -> 扩展仓库源 的反查表。

    来源优先级：
      1) 同目录下 node_map.json（本地缓存，可离线用）
      2) ComfyUI-Manager 的 extension-node-map.json（本地已装则直接用）
    数据格式：{节点类型: 仓库/来源}
    """
    global _NODE_MAP_CACHE
    if _NODE_MAP_CACHE is not None:
        return _NODE_MAP_CACHE

    here = os.path.dirname(os.path.abspath(__file__))
    # 先用已经扁平化好的 node_map.json（节点类型 -> 来源）
    flat = os.path.join(here, "node_map.json")
    if os.path.exists(flat):
        try:
            d = json.load(open(flat, encoding="utf-8"))
            if isinstance(d, dict) and d:
                _NODE_MAP_CACHE = d
                return d
        except Exception:
            pass

    # 退回原始 extension-node-map.json（仓库 -> [节点列表, 元信息]）
    raw_candidates = [
        r"D:/ComfyUI-aki-v3-2026/ComfyUI-aki-v3/ComfyUI/custom_nodes/ComfyUI-Manager/extension-node-map.json",
        os.path.expanduser(
            "~/AppData/Local/Programs/Krea2出图工作台/kernel/ComfyUI/custom_nodes/"
            "ComfyUI-Manager/extension-node-map.json"),
    ]
    for p in raw_candidates:
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        m: dict[str, str] = {}
        for repo, v in d.items():
            if isinstance(v, list) and v and isinstance(v[0], list):
                for t in v[0]:
                    if isinstance(t, str):
                        m.setdefault(t, repo)
        if m:
            _NODE_MAP_CACHE = m
            return m
    _NODE_MAP_CACHE = {}
    return {}


def friendly_source(src: str) -> str:
    """把仓库 url 变成人可读的名字"""
    if not src:
        return ""
    s = src.rstrip("/")
    if "github.com/" in s:
        parts = s.split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            return f"github.com/{parts[0]}/{parts[1]}"
    if s.startswith("https://gist."):
        return "gist（单文件节点包）"
    if s.startswith("file://"):
        return "本地文件"
    return s[:80]


# ---------------------------------------------------------------- 工作流解析

def detect_format(d: dict) -> str:
    if not isinstance(d, dict):
        return "unknown"
    if "nodes" in d and isinstance(d["nodes"], list):
        return "ui"
    if "prompt" in d and isinstance(d["prompt"], dict):
        return "api"
    if any(isinstance(v, dict) and "class_type" in v for v in d.values()):
        return "api"
    return "unknown"


def collect_ui_nodes(d: dict) -> tuple[list[dict], list[dict]]:
    """返回 (顶层节点, subgraph 内部节点)，节点统一成 {id,type,inputs,widgets_values}"""
    nodes = list(d.get("nodes") or [])
    sub_nodes: list[dict] = []
    subgraph_ids = set()
    defs = (d.get("definitions") or {}).get("subgraphs") or []
    for sg in defs:
        sid = sg.get("id")
        if sid:
            subgraph_ids.add(sid)
        for n in sg.get("nodes") or []:
            n2 = dict(n)
            n2["_in_subgraph"] = sg.get("name") or sid
            sub_nodes.append(n2)
    return nodes, sub_nodes, subgraph_ids


def collect_api_nodes(d: dict) -> list[dict]:
    out = []
    for nid, n in d.items():
        if not isinstance(n, dict) or "class_type" not in n:
            continue
        out.append({
            "id": nid,
            "type": n["class_type"],
            "inputs": {k: {"name": k, "link": v if isinstance(v, list) else None, "_value": v}
                       for k, v in (n.get("inputs") or {}).items()},
            "widgets_values": None,
            "_api": True,
        })
    return out


# ---------------------------------------------------------------- 检查

def node_required_inputs(oi_node: dict) -> dict:
    return (oi_node.get("input") or {}).get("required") or {}


def input_option(oi_node: dict, name: str) -> dict:
    req = (oi_node.get("input") or {}).get("required") or {}
    opt = (oi_node.get("input") or {}).get("optional") or {}
    for src in (req, opt):
        if name in src:
            v = src[name]
            return v[1] if len(v) > 1 and isinstance(v[1], dict) else {}
    return {}


def is_model_input(name: str, oi_node: dict) -> bool:
    """判断某个输入是不是「本机文件名枚举」类输入"""
    if name in MODEL_INPUT_HINTS:
        return True
    v = (oi_node.get("input") or {}).get("required", {}).get(name)
    if not v:
        return False
    if isinstance(v[0], list) and v[0] and isinstance(v[0][0], str):
        # 枚举型 COMBO：如果选项看起来像文件名（含 .safetensors/.pth/.ckpt/.gguf）
        return any(isinstance(x, str) and x.lower().endswith(
            (".safetensors", ".pth", ".ckpt", ".pt", ".bin", ".gguf", ".onnx")) for x in v[0])
    return False


def enum_options(oi_node: dict, name: str) -> list:
    req = (oi_node.get("input") or {}).get("required") or {}
    opt = (oi_node.get("input") or {}).get("optional") or {}
    for src in (req, opt):
        if name in src:
            v = src[name]
            if isinstance(v[0], list):
                return v[0]
    return []


def python_module_of(oi_node: dict) -> str:
    m = oi_node.get("python_module") or ""
    return m


def check_workflow(path: str, object_info: dict, report: Report) -> None:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception as e:
        report.findings.append(Finding("error", "broken_link", None, "(文件)", f"JSON 无法解析: {e}",
                                       "文件可能损坏或被编辑器改写；重新从来源下载"))
        return

    fmt = detect_format(d)
    report.format = fmt
    if fmt == "ui":
        top, sub, sg_ids = collect_ui_nodes(d)
        nodes = [n for n in top if n.get("type") not in sg_ids] + sub
    elif fmt == "api":
        nodes = collect_api_nodes(d)
    else:
        report.findings.append(Finding("error", "broken_link", None, "(格式)",
                                       "认不出这是 ComfyUI 工作流（既不是 UI 格式也不是 API 格式）",
                                       "确认文件是 .json 工作流，不是网页或图片"))
        return

    report.node_count = len(nodes)
    known = set(object_info.keys())
    node_map = load_node_map()

    # 收集可用模型清单（每个输入名 -> 选项集合）
    for n in nodes:
        t = n.get("type")
        nid = n.get("id")
        # ---- 1. 缺节点
        if t not in known:
            if t in FRONTEND_ONLY_NODES:
                continue
            src = node_map.get(str(t), "")
            src_txt = friendly_source(src)
            if src_txt:
                fix = f"安装扩展：{src_txt}（ComfyUI-Manager 搜该仓库名，或 git clone 到 custom_nodes）"
            else:
                fix = "该节点未收录在扩展库中，可能需要联系工作流作者索要依赖"
            report.findings.append(Finding(
                "error", "missing_node", nid, str(t),
                f"节点类型 `{t}` 本机未安装"
                + (f"（来自子图 `{n.get('_in_subgraph')}`）" if n.get("_in_subgraph") else "")
                + (f"\n      需要的扩展：{src_txt}" if src_txt else ""),
                fix))
            continue

        oi = object_info[t]
        required = node_required_inputs(oi)

        # ---- 2/4/5: 模型缺失 + 必需输入缺值 + 硬编码路径
        vals: dict[str, Any] = {}

        if n.get("_api"):
            for k, v in (n.get("inputs") or {}).items():
                if isinstance(v, dict) and v.get("_value") is not None and not isinstance(v["_value"], list):
                    vals[k] = v["_value"]
        else:
            # widgets_values 对应 required 里「非连线类型」的参数，顺序一致
            wv = n.get("widgets_values")
            widget_names = []
            for k, v in required.items():
                typ = v[0]
                if isinstance(typ, list) or (isinstance(typ, str) and typ in WIDGET_TYPES):
                    widget_names.append(k)
            if isinstance(wv, list):
                for k, val in zip(widget_names, wv):
                    vals[k] = val
            elif isinstance(wv, dict):
                vals.update(wv)

        for k, val in vals.items():
            if not isinstance(val, str) or not val.strip():
                continue
            oi_opt = enum_options(oi, k)
            if is_model_input(k, oi):
                if oi_opt and val not in oi_opt:
                    # 文件名大小写 / 路径分隔符归一后再比
                    norm = {str(x).replace("/", "\\").lower() for x in oi_opt}
                    if val.replace("/", "\\").lower() not in norm:
                        base = val.replace("\\", "/").split("/")[-1]
                        similar = [x for x in oi_opt if base.lower() in str(x).replace("\\", "/").split("/")[-1].lower()]
                        report.findings.append(Finding(
                            "error", "missing_model", nid, t,
                            f"模型文件不存在：`{val}`\n"
                            + (f"      本机有相似文件，可能是路径不同：{similar[0]}" if similar else
                               f"      该参数本机共 {len(oi_opt)} 个可用文件"),
                            f"把模型放到对应 models 目录，或在节点里改选本机已下载的：{base}"))
            # 本机绝对路径写死
            if (os.path.isabs(val) or (len(val) > 2 and val[1] == ":")) and not os.path.exists(val):
                report.findings.append(Finding(
                    "warn", "missing_file", nid, t,
                    f"写死了本机路径且文件不存在：`{val}`",
                    "改成相对路径（如放 input 目录后用文件名），否则换台机器必然失败"))

        # ---- 4. 必需输入没值
        if not n.get("_api"):
            provided = set(vals.keys())
            inputs = n.get("inputs") or []
            linked = {i.get("name") for i in inputs if i.get("link") is not None}
            for k, v in required.items():
                typ = v[0]
                is_widget = isinstance(typ, list) or (isinstance(typ, str) and typ in WIDGET_TYPES)
                if not is_widget:
                    continue
                if k in provided or k in linked:
                    continue
                opt = input_option(oi, k)
                if opt.get("default") is not None:
                    continue  # 有默认值，ComfyUI 会补
                report.findings.append(Finding(
                    "warn", "missing_widget", nid, t,
                    f"必需参数 `{k}` 没有值，也没有默认值",
                    "在工作流里手工填该参数，或重新连线"))


def print_report(r: Report, verbose: bool = True) -> None:
    print("=" * 72)
    print(f"文件: {r.path}")
    print(f"格式: {r.format}   节点数: {r.node_count}   "
          f"错误: {len(r.errors)}  警告: {len(r.warns)}")
    print("=" * 72)
    if r.ok() and not r.warns:
        print("✅ 没有发现问题")
        return
    for f in r.findings:
        icon = "❌" if f.severity == "error" else "⚠️ "
        print(f"{icon} [{f.kind}] 节点 {f.node_id} ({f.node_type})")
        for line in f.detail.split("\n"):
            print(f"    {line}")
        if f.fix and verbose:
            print(f"    → 修复: {f.fix}")
        print()


# ---------------------------------------------------------------- 入口

def cmd_check(args) -> int:
    object_info = fetch_object_info(args.host)
    r = Report(path=args.file)
    check_workflow(args.file, object_info, r)
    if args.json:
        print(json.dumps({
            "path": r.path, "format": r.format, "node_count": r.node_count,
            "ok": r.ok(),
            "findings": [f.__dict__ for f in r.findings],
        }, ensure_ascii=False, indent=2))
    else:
        print_report(r)
    return 0 if r.ok() else 1


def cmd_dir(args) -> int:
    object_info = fetch_object_info(args.host)
    files = []
    for root, _, fs in os.walk(args.dir):
        for f in fs:
            if f.lower().endswith(".json") and "node_modules" not in root:
                files.append(os.path.join(root, f))
    total_ok = 0
    results = []
    for p in files:
        r = Report(path=p)
        check_workflow(p, object_info, r)
        results.append(r)
        if r.ok():
            total_ok += 1
    print(f"共 {len(files)} 个 json：健康 {total_ok}，有问题 {len(files)-total_ok}\n")
    # 按问题类型汇总
    agg: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            agg[f.kind + ":" + f.severity] = agg.get(f.kind + ":" + f.severity, 0) + 1
    for k, v in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  {k:<28} {v}")
    print()
    # 缺哪些节点（去重，按出现次数排）—— 这是用户最想看的
    miss_nodes: dict[str, int] = {}
    miss_models: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            if f.kind == "missing_node":
                miss_nodes[f.node_type] = miss_nodes.get(f.node_type, 0) + 1
            elif f.kind == "missing_model":
                name = f.detail.split("`")[1] if "`" in f.detail else f.detail
                miss_models[name] = miss_models.get(name, 0) + 1
    if miss_nodes:
        node_map = load_node_map()
        by_ext: dict[str, list[str]] = {}
        unknown = []
        for t in miss_nodes:
            src = node_map.get(t, "")
            if src:
                by_ext.setdefault(friendly_source(src), []).append(t)
            else:
                unknown.append(t)
        print("=" * 72)
        print(f"需要安装的扩展（{len(by_ext)} 个）—— 装完这些，绝大多数工作流就能跑：")
        print("=" * 72)
        for ext, ts in sorted(by_ext.items(), key=lambda x: -len(x[1])):
            covered = sum(miss_nodes[t] for t in ts)
            print(f"\n  ▶ {ext}")
            print(f"     提供 {len(ts)} 种节点，覆盖 {covered} 处缺失")
            print(f"     节点：{', '.join(sorted(ts)[:8])}{' …' if len(ts) > 8 else ''}")
        if unknown:
            print(f"\n  ▶ 未收录在扩展库（{len(unknown)} 种，需联系作者）：")
            print(f"     {', '.join(sorted(unknown)[:12])}")
        print()
    if miss_models:
        print(f"缺失模型（共 {len(miss_models)} 个，出现最多的 15 个）：")
        for k, v in sorted(miss_models.items(), key=lambda x: -x[1])[:15]:
            print(f"  {v:>4} 次  {k}")
        print()
    worst = sorted([r for r in results if not r.ok()], key=lambda r: -len(r.errors))[:5]
    if worst:
        print("问题最多的 5 个：")
        for r in worst:
            print(f"  {len(r.errors):>3} 错  {r.path}")
    return 0


def _git_url(src: str) -> str:
    """把扩展来源转成可 git clone 的地址"""
    if not src:
        return ""
    s = src
    if "github.com/" in s:
        base = "https://github.com/" + s.split("github.com/")[-1]
        # 去掉可能的 tree/ 子路径和末尾内容
        parts = base.rstrip("/").split("/")
        if len(parts) >= 5:
            base = "/".join(parts[:5])
        return base
    if s.startswith("http") and not s.startswith("https://gist"):
        return s
    return ""


def cmd_fix(args) -> int:
    """根据体检结果生成安装脚本 + 下载清单"""
    object_info = fetch_object_info(args.host)
    node_map = load_node_map()

    targets: list[str] = []
    if os.path.isdir(args.path):
        for root, _, fs in os.walk(args.path):
            for f in fs:
                if f.lower().endswith(".json") and "node_modules" not in root:
                    targets.append(os.path.join(root, f))
    else:
        targets.append(args.path)

    ext_count: dict[str, int] = {}
    model_count: dict[str, int] = {}

    for p in targets:
        r = Report(path=p)
        check_workflow(p, object_info, r)
        for f in r.findings:
            if f.kind == "missing_node":
                src = node_map.get(f.node_type, "")
                url = _git_url(src)
                if url:
                    ext_count[url] = ext_count.get(url, 0) + 1
            elif f.kind == "missing_model":
                name = f.detail.split("`")[1] if "`" in f.detail else ""
                if name:
                    model_count[name] = model_count.get(name, 0) + 1

    if not ext_count and not model_count:
        print(f"✅ 检查了 {len(targets)} 个工作流，没有发现需要安装的东西。")
        return 0

    outdir = args.out or "."
    os.makedirs(outdir, exist_ok=True)

    # 1) Windows 安装脚本
    bat = os.path.join(outdir, "install_node_extensions.bat")
    with open(bat, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("@echo off\nchcp 65001 >nul\n")
        fh.write("REM wfdoctor 生成的扩展安装脚本\n")
        fh.write('REM 用法: 把 COMFY_DIR 改成你的 ComfyUI 目录，然后双击运行\n\n')
        fh.write('set COMFY_DIR=E:\\ComfyUI\n')
        fh.write('if not exist "%COMFY_DIR%\\custom_nodes" (\n')
        fh.write('  echo [错误] 找不到 %COMFY_DIR%\\custom_nodes，请先改本文件里的 COMFY_DIR\n')
        fh.write('  pause & exit /b 1\n)\n\n')
        for url in sorted(ext_count, key=lambda u: -ext_count[u]):
            name = url.rstrip("/").split("/")[-1].replace(".git", "")
            fh.write(f'echo [安装] {name}  (需要 {ext_count[url]} 处缺失)\n')
            fh.write(f'if not exist "%COMFY_DIR%\\custom_nodes\\{name}" (\n')
            fh.write(f'  git clone {url} "%COMFY_DIR%\\custom_nodes\\{name}"\n')
            fh.write(') else (\n')
            fh.write(f'  echo   已存在，跳过\n)\n\n')
        fh.write('echo.\necho 全部完成。请重启 ComfyUI。\npause\n')

    # 2) bash 版本
    sh = os.path.join(outdir, "install_node_extensions.sh")
    with open(sh, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\n")
        fh.write("# wfdoctor 生成的扩展安装脚本\n")
        fh.write('COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"\n')
        fh.write('set -e\n\n')
        for url in sorted(ext_count, key=lambda u: -ext_count[u]):
            name = url.rstrip("/").split("/")[-1].replace(".git", "")
            fh.write(f'echo "[安装] {name} (需要 {ext_count[url]} 处缺失)"\n')
            fh.write(f'[ -d "$COMFY_DIR/custom_nodes/{name}" ] || git clone {url} "$COMFY_DIR/custom_nodes/{name}"\n')
        fh.write('\necho "全部完成，请重启 ComfyUI"\n')

    # 3) 模型清单
    mp = os.path.join(outdir, "missing_models.txt")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write("# wfdoctor 生成的缺失模型清单\n")
        fh.write("# 说明：模型通常不能靠脚本自动下载（多数需要登录 / 授权），\n")
        fh.write("#       这里列出文件名和出现次数，方便你在 Civitai / HuggingFace 上逐个找。\n")
        fh.write("# 格式：出现次数  文件名\n\n")
        for name, cnt in sorted(model_count.items(), key=lambda x: -x[1]):
            fh.write(f"{cnt:>5}  {name}\n")

    # 4) 汇总报告
    rp = os.path.join(outdir, "wfdoctor_report.md")
    with open(rp, "w", encoding="utf-8") as fh:
        fh.write("# wfdoctor 体检报告\n\n")
        fh.write(f"- 检查工作流：{len(targets)} 个\n")
        fh.write(f"- 需要安装扩展：{len(ext_count)} 个\n")
        fh.write(f"- 缺失模型：{len(model_count)} 个\n\n")
        fh.write("## 需要安装的扩展\n\n")
        for url in sorted(ext_count, key=lambda u: -ext_count[u]):
            fh.write(f"- [ ] {url} —— 影响 {ext_count[url]} 处\n")
        fh.write("\n## 缺失模型（前 30）\n\n")
        for name, cnt in sorted(model_count.items(), key=lambda x: -x[1])[:30]:
            fh.write(f"- [ ] `{name}` —— 影响 {cnt} 处\n")

    print(f"已生成 4 个文件到 {os.path.abspath(outdir)}：")
    print(f"  1. install_node_extensions.bat   —— Windows 一键装扩展（改一行 COMFY_DIR 即可）")
    print(f"  2. install_node_extensions.sh    —— Linux/Mac 版")
    print(f"  3. missing_models.txt            —— 缺失模型清单（{len(model_count)} 个）")
    print(f"  4. wfdoctor_report.md            —— 完整报告（可勾选）")
    print()
    print(f"覆盖范围：{len(ext_count)} 个扩展即可补齐所有缺失节点。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="wfdoctor",
        description="ComfyUI 工作流体检器：一条命令找出缺节点 / 缺模型 / 断线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--host", default="http://127.0.0.1:8188",
                    help="正在运行的 ComfyUI 地址（默认 %(default)s）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("check", help="检查单个工作流")
    p1.add_argument("file")
    p1.set_defaults(func=cmd_check)

    p2 = sub.add_parser("check-dir", help="批量检查目录下所有 json")
    p2.add_argument("dir")
    p2.set_defaults(func=cmd_dir)

    p3 = sub.add_parser("fix", help="生成修复脚本（装扩展的 bat/sh + 模型清单 + 报告）")
    p3.add_argument("path", help="工作流文件或目录")
    p3.add_argument("--out", "-o", default="wfdoctor_fix", help="输出目录（默认 %(default)s）")
    p3.set_defaults(func=cmd_fix)

    args = ap.parse_args()
    # 让 --host/--json 对子命令生效
    with_host = argparse.Namespace(**vars(args))
    try:
        return args.func(with_host)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

