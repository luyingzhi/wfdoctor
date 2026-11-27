# Changelog

## 1.0.0 — 2026-11-27

首个可发布版本。

### 功能
- `check <file>` 单个工作流体检
- `check-dir <dir>` 目录批量体检（含子目录）
- `--json` 机器可读输出，可接 CI
- `--host` 指定 ComfyUI 地址（默认 `http://127.0.0.1:8188`）

### 检查项
- 缺自定义节点，并**反查所属扩展仓库**（4 万+ 节点类型映射）
- 缺模型文件（UNET / LoRA / VAE / CLIP / Checkpoint / upscale / controlnet 等枚举型输入）
- 断线（输入被引用但无连线）
- 必需参数缺值
- 写死的本机绝对路径

### 支持格式
- UI 格式（`nodes` 数组）
- API 格式（`class_type` 结构）
- 含 `definitions.subgraphs` 的新版格式，子图内部节点一并检查

### 验证
- 11 项单元测试全绿
- 对 535 个真实工作流做过批量实测：识别出 89 个需要安装的扩展、336 个缺失模型
