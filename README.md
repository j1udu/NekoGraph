# NekoGraph

A LangGraph-native QQ Agent Framework powered by NapCat and OneBot v11.

## 已实现

- OneBot v11 反向 WebSocket 接入与统一消息模型
- 私聊、群聊唤醒及 Conversation 隔离
- LangGraph 多轮对话与 SQLite Checkpoint
- 命令路由、Tool Registry、风险审批与 Interrupt/Resume
- 本地终端聊天和 Vue 3 + FastAPI 管理界面
- OpenAI-compatible 多模型配置、导入与热切换

## 本地部署

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/j1udu/NekoGraph.git
cd NekoGraph
uv sync
uv run nekograph dashboard
```

管理界面默认地址：<http://127.0.0.1:5190>

可在管理界面的“模型”页面添加 OpenAI-compatible 模型；未配置时使用 Fake Model。

其他启动方式：

```bash
uv run nekograph chat     # 本地终端对话
uv run nekograph gateway  # OneBot Gateway，默认端口 8080
```
