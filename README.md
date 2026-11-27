# wfdoctor

**一条命令查出 ComfyUI 工作流为什么跑不起来。**

从网上（Civitai、GitHub、朋友）下载的 ComfyUI 工作流，直接拖进界面，最常见的三种失败：

- 满屏红色节点 —— 缺自定义节点
- 某个节点报 `Value not in list` —— 缺模型文件
- 跑起来报错 —— 输入没接上 / 参数没填

`wfdoctor` 把这三件事一次性告出来，**还会告诉你缺的节点属于哪个扩展包**，不用一个个去搜。

---

## 用法

```bash
# 1. 先启动 ComfyUI（wfdoctor 需要读它的 /object_info 才知道你本机有什么）
# 2. 体检单个工作流
python wfdoctor.py check my_workflow.json

# 3. 批量体检一个目录（含子目录）
python wfdoctor.py check-dir ./workflows

# 4. 输出机器可读 JSON（接 CI / 给 agent 用）
python wfdoctor.py --json check my_workflow.json

# 5. ComfyUI 不在默认端口
python wfdoctor.py --host http://127.0.0.1:9091 check my_workflow.json
```

---

## 输出示例

批量检查 535 个工作流：

```
共 535 个 json：健康 1，有问题 534

  missing_node:error           3247
  missing_model:error          1411
  missing_file:warn            28

======================================================================
需要安装的扩展（89 个）—— 装完这些，绝大多数工作流就能跑：
======================================================================

  ▶ github.com/kijai/ComfyUI-WanVideoWrapper
     提供 72 种节点，覆盖 930 处缺失

  ▶ github.com/Lightricks/ComfyUI-LTXVideo
     提供 19 种节点，覆盖 92 处缺失

  ▶ github.com/LeonQ8/ComfyUI-Dynamic-Lora-Scheduler
     提供 12 种节点，覆盖 562 处缺失
```

单个工作流：

```
❌ [missing_node] 节点 84 (WanVideoEncode)
    节点类型 `WanVideoEncode` 本机未安装
          需要的扩展：github.com/kijai/ComfyUI-WanVideoWrapper
    → 修复: 安装扩展：github.com/kijai/ComfyUI-WanVideoWrapper

❌ [missing_model] 节点 12 (UNETLoader)
    模型文件不存在：`Krea2\krea2_turbo_fp8_scaled.safetensors`
    → 修复: 把模型放到对应 models 目录，或改选本机已下载的
```

---

## 能查什么

| 检查项 | 说明 |
|---|---|
| 缺自定义节点 | 对照本机 `/object_info`，**并反查它属于哪个 GitHub 扩展** |
| 缺模型文件 | UNET / LoRA / VAE / CLIP / Checkpoint / upscale / controlnet 等全部枚举型输入 |
| 断线 | 输入被引用但没有连线 |
| 必需参数为空 | 没填值也没默认值的控件 |
| 写死的本机路径 | `D:\xxx\model.safetensors` 这类换机必挂的路径 |

## 支持的工作流格式

- **UI 格式**（界面里 Save 出来的，含 `nodes` 数组）
- **API 格式**（`/prompt` 提交的那个，`class_type` 结构）
- **含 subgraph 的新版格式**（ComfyUI 0.3.5+ 的 `definitions.subgraphs`，子图内部节点也会一起查）

## 为什么不是又一个"重复造轮子"

- ComfyUI-Manager 的 Install Missing Custom Nodes 只在你**已经打开**工作流、并且缺失节点时才有用；它是 GUI 按钮，不能批量、不能进 CI、不能给 agent 用。
- `wfdoctor` 是**离线 CLI**：可以对一整个目录跑，可以接进构建流程，可以输出 JSON。
- 节点→扩展的反查表直接来自 ComfyUI-Manager 的 `extension-node-map.json`（4 万多个节点类型），所以**提示的扩展名是准的**，不是猜的。

## 依赖

- Python 3.9+
- 无第三方包（纯标准库）
- 需要本机 ComfyUI 正在运行（用它做"本机有什么"的真值来源）

## 许可证

MIT

## 已知限制

- 判断"模型缺失"依赖你启动的 ComfyUI 的 `/object_info`，所以**换一个内核就要用那个内核的地址**（`--host`）。
- 只做静态检查，不执行工作流。
- 极少数前端虚拟节点（Reroute / Note 等）已内置白名单，不会误报；但新出现的第三方 UI-only 节点可能需要补充白名单。
