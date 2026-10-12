# AiTaskPlatform 离线评测

把「AI 答得准不准 / 判得准不准」变成**可复跑的数字**，而不是每次靠人看一遍。

## 为什么要有它

三个现实：

1. **单跑一次 pass/fail 不构成结论。** 同一个输入，`deepseek-v4-flash` + `temperature=0`
   实测能给出完全相反的判定（同一案例 6/6 不通过 ↔ 0/8 不通过）。所以这里必须看
   **多轮一致率**，并且把**翻转的用例**点名列出来。
2. **误杀和漏放后果不同。** 自评把**正确**回答判成不通过 → 触发重写 → 真的把回答改坏
   （生产踩过，见 [../issues.md](../issues.md) #1–#3）；漏放只是放过幻觉。只报一个总准确率
   会把这两件事糊在一起，不知道该往哪边调判据。
3. **判据改了要能立刻看到代价。** 数据集里的每个「应通过」用例都埋了**旧诊断口径会误判的
   陷阱**（比如「证据里还有别的内容没覆盖」「证据里的祈使句没被复述」）。所以这份数据集
   本身就是那次修复的**回归网**——口径退回旧行为，一致率/准确率会掉下来。

## 怎么跑

```bash
# 看有哪些用例（不跑，不连网）
python -m ai.agents.AiTaskPlatform.eval.run_eval --list

# 全量跑 3 轮
python -m ai.agents.AiTaskPlatform.eval.run_eval --dataset evaluator_verdicts --rounds 3

# 只跑某几条（id 精确或前缀匹配），多跑几轮看稳定性
python -m ai.agents.AiTaskPlatform.eval.run_eval --only discuss-contradicts,discuss-invents --rounds 5

# 落明细，给复盘用
python -m ai.agents.AiTaskPlatform.eval.run_eval --rounds 3 --json /tmp/eval.json

# 当门槛用（CI 里可选）
python -m ai.agents.AiTaskPlatform.eval.run_eval --rounds 3 --fail-under-consistency 0.9 --fail-under-accuracy 0.8
```

退出码：`0` 正常 / `1` 未达 `--fail-under-*` 门槛 / `2` 用法或数据集错误。

**要求**：能调到 LLM（读 `ai/.env`）。**不需要**数据库、不需要 AI 服务在跑——
用例集是随仓提交的 JSON。指标层（[metrics.py](metrics.py)）是纯函数，单测不需要网络：
`python -m pytest ai/agents/AiTaskPlatform/eval/test_metrics.py -q`。

## 指标怎么读

```
准确率（全部轮次）  33/36 = 91.7%
  其中首轮          11/12 = 91.7%
  ❌ 误杀（正确被判不通过、会触发重写） 2
  ⚠️ 漏放（幻觉/偏题被判通过）         1
  未解析（调用或解析失败）             0

多轮一致率          10/12 = 83.3%
  ⚠️ 翻转用例（判据在这类输入上不可靠）：discuss-generic-template
```

- **准确率**分母是解析成功的判定次数；`未解析`（调用失败/解析失败）单列，**不塞进分母**，
  也不美化成通过或不通过——「判官没跑起来」和「判官判错了」是两件事。
- **一致率 < 100% 时先看翻转清单**：它说明这类输入下判据不可靠，而不是模型偶尔手滑。
- 首轮单独列出来，是为了和「跑一次就下结论」的旧习惯对齐：那正是要避免的口径。

## 加用例

往 `datasets/<名字>.json` 的 `cases` 里加一条：

```json
{
  "case_id": "discuss-<说清场景>",
  "mode": "discuss",              // discuss | diagnose —— 决定用哪套判据
  "context": "工单 #… 用户问：…",
  "evidence": "模型生成时看得到的事实来源（讨论史/附件/检索/记忆）",
  "draft": "待评的回答",
  "expected": true,                // 人工标注的「正确判法」：true=应通过
  "why": "判据是什么，为什么这么标"
}
```

规矩：

- **写明 `why`**——判据本身可以被讨论，别让后人猜你为什么这么标；
- **只用合成数据**，不要抄真实工单/用户信息（占位名如 `工程师A`、工单号如 `#10001`）；
- 优先加**陷阱用例**（容易被判错的那种），它们比明显错的用例有价值得多；
- 新用例类型（另一种 `kind`）= 在 [run_eval.py](run_eval.py) 里加一个执行器 +
  在 `RUNNERS` 登记一行，数据集里用 `kind` 指过来。

## 已知边界

- **不是单元测试**：要真实 LLM，慢且会飘，不要塞进 `pytest` 默认收集范围（本目录只有
  `test_metrics.py` 是单测）。
- **不是全量质量评估**：目前只覆盖自评判定（`kind=evaluator`）。能力调度质量
  （监督者 plan 该派谁）、日志分析结论质量还没进数据集——需要各自先有「人工标注的期望」。
- **样本量小**：12 条用例足以发现「判据整体方向错了」，不足以给出稳定的百分比结论；
  看到 91.7% 这种数不要当精度用，看趋势和翻转清单。
