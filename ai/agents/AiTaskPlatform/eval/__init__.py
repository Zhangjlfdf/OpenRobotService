"""eval — AiTaskPlatform 离线评测（用例集 + 指标 + 多轮稳定性）。

跑法见 [README.md](README.md)，或 `python -m ai.agents.AiTaskPlatform.eval.run_eval --help`。

本 `__init__` 刻意不 import 任何子模块：`metrics` 要能被单测直接导入，
而 `run_eval` 会加载 .env、连 LLM，不该被 import 行为顺带拉进来。
"""
