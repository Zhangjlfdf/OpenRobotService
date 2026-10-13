# AiTaskPlatform 事故台账

> 与 [待做.md](待做.md) 分工：**待做**记「还要做什么」；本文件记「已经踩过什么坑、根因是什么、怎么修的」。
> 唯一目的：同一个坑不踩第二次。新增条目按日期开章，用「问题 / 根因 / 修复」三列，修复必须带证据（提交号 / 测试名 / 日志特征）。
> 写法参照 [../AiDiagnosisPlatform/issues.md](../AiDiagnosisPlatform/issues.md)——那份 400 行的 #1–#30 更细，**值得一并读**，我们下面有 4 条教训就是从它那里抄来的。
> 本文件以 AiTaskPlatform 为主；跨平台、或真实位置在诊断侧的条目集中在最后一章，**标明真实位置，不冒领**。

## 当前基线（先看这几条，能少走弯路）

| 项 | 事实 |
|----|------|
| 测试 | `python -m pytest -q` → **855 passed, 27 skipped**。跳过的是需要本地 Qdrant 数据的检索用例 |
| 进程模型 | [../../run.py:446](../../run.py:446) 的 `uvicorn.run` **没有 `workers=`** → **单进程**。所以「进程内缓存/快照」方案成立（[runtime/inject_mailbox.py](runtime/inject_mailbox.py)、[runtime/report_cache.py](runtime/report_cache.py)），但也意味着**重启即失** |
| `.env` | 未纳入版本控制（`.gitignore:22`）。改了必须**手动改 + 重启 AI 服务**，`load_dotenv` 只在启动时读一次 |
| 过程区数据契约 | 后端 [tracing/trace.py](tracing/trace.py) 的 `nest_progress_todos` 产出「分析 →（规划 + 各能力）→ 子步骤/事件」；前端 `frontend/src/shared/components/DiscussionPanel.tsx` 的 `ProgressTodoItem` **递归渲染 `children`**，后端行自带的 `status`/`phase` 优先于 span 状态 |
| 两个 Agent 的关系 | AiTaskPlatform 与 AiDiagnosisPlatform **零交叉导入**，同一件事存在两份独立实现（见最后一章「刻意不共用」） |
| 怎么验证 LLM 行为 | 用 [eval/](eval/)（**判定稳定性** + 每条评审意见的**落点**，落点用 prompt 复核）。**单跑一次 pass/fail 不构成结论**——`temperature=0` 也能给出相反判定（见 #1–#3）。也**别用「期望通过/不通过」那套**：标准随提问变，实测无区分力。纯函数指标单测不需要 LLM |

## 2026-10-11 讨论问答：初稿总被自评判不通过（#1–#3）

现象：讨论区里 AI 的思考过程明显正确，但最终答复反复被 Evaluator 打回重写。

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | 自评判据套的是「诊断报告」口径，与讨论问答场景不匹配 | 判据生成没有按场景适配 | 判据按场景适配（`capabilities/core/evaluator.py`，测试 `capabilities/core/test_evaluator.py`） |
| 2 | evidence 里漏了讨论史，判据拿不到判断依据 | 拼 evidence 时只取附件与检索结果 | 讨论史补进 evidence |
| 3 | 判错时把本来正确的回答覆盖掉了 | 重写路径无条件采纳自评结论 | 判错时保护原稿 |

**教训**：`deepseek-v4-flash` + `temperature=0` **仍然没有确定性**——同一输入实测出现「6/6 不通过」与「0/8 不通过」两种极端。**pass/fail 计数不能当验证手段**，要看多轮稳定性（诊断侧的做法值得抄：给场景编号，跑 3 轮看稳定性，见 [../AiDiagnosisPlatform/issues.md](../AiDiagnosisPlatform/issues.md) 结尾）。

**2026-10-12 用新建的 [eval/](eval/) 复核（含负结果）**：12 条用例 × 2 轮 = **24 次判定、12/12 两轮同判、20/20 条评审意见复核有落点、0 复核失败**，说明这几处修复目前站得住。但复核同时暴露两件事：

- **当初那个故障模式现在复现不出来**：把判据换回诊断口径（`mode="diagnose"`）、甚至把护栏 `_JUDGE_RULES` 整个抽掉，这些用例**照样全部通过**；专门构造「证据塞 8 条无关话题 + 草稿只答一句」的极端输入，三种判据配置也全判通过。所以**手写的陷阱没有区分力，我们还没有真正的回归网**——要建，得从生产日志里捞真实的 evidence/draft 对与当时的判定。
- **量法也换了**：原本按「期望通过/不通过」算准确率，这是错的工具（标准随提问变），已删除。现在只量①判定稳定性②每条评审意见的落点，且落点**用 prompt 复核**（`Evaluator.verify_issues`）而不是字符串匹配——实测机械比对在这件事上 20 条一条都核对不上（换词复述算成没落点、常用词又凑出假重叠）。

详见 [eval/README.md](eval/README.md) 的「为什么」与「已知边界」。

## 2026-10-12 评论作者名全部丢失：`【】正文`（#4）

**现象**：讨论史每条评论渲染成 `【】正文`，LLM 分不清哪句话是谁说的，讨论结论容易张冠李戴。

**根因**：`TaskComment` **没有 `created_by_name` 列**（后端 API 的该字段是派生值），`getattr(c, "created_by_name", "")` 恒为空串；而 `task_comments.created_by` 里存的是 `wechat_{openid[:10]}`。这行 `getattr` 在 5 处出现，全是死代码。

**修复**（`92da3e84`，测试 `contexts/test_comments_author.py`）：用 `UserService.get_user_map()` 反查真名——它的 key **同时包含 id 和 username**，所以微信 ID 与内部账号都能命中；映射里没有的作者保留原始 ID（不吞掉，便于排查）；完全没有作者时占位 `【?】`。

**教训**：`getattr(obj, "field", "")` 这类「防御性读取」会把「模型里根本没有这个字段」伪装成「这里暂时没值」，静默死代码就是这么长出来的。**取字段前先确认模型/接口里真有这个字段。**

## 2026-10-12 两处「以为只剩前端 / 以为已经做了」的判断失误（#5–#6）

| # | 我原来的判断 | 实际 | 真相怎么暴露的 |
|---|------|------|------|
| 5 | [待做.md](待做.md) 写 P3b「后端已全量挂 span，**只剩前端**把父子树渲染出来」 | 前端 `ProgressTodoItem` **早就递归渲染 `children`**；真正缺的是后端 `nest_progress_todos` 只按 span **名字**把事件挂到能力行下，没照真实层级重排 | 读前端 `applyAiProgress` → `setAiTodos(incoming)` 原样上树，没有任何拍平 |
| 6 | [待做.md](待做.md) 有 4 条写着「没做」 | 全部**已做并接线**（审批策略、7 步命令、P4 双队列、车型与调度版本注入日志子 Agent） | 逐条 grep 代码 + 调用点 |

**教训**：「文档说没做」不等于没做；「只剩前端」这种**把工作量判在别人身上**的结论尤其要先读对方代码再下。修复提交 `7f34e384`、`68c61bcd`。

## 2026-10-12 `@#` 引用拿不到「帮我分析」报告（#7）

**现象**：讨论区用 `@#` 引用一个工单时，用户常问「他上次那份分析报告说了啥」，而报告**不落库**，事后谁都拿不到，LLM 只能编。

**根因**：diagnose 每次现算报告、有意不落库（报告是那一轮的结论，入库会带来过期与污染）；原实现的 docstring 写着「故不注入、也让 LLM 不要假装看到了它」——**但这条口径从没真正发给过 LLM**，只存在于注释里。

**修复**（`7f34e384`）：新增 [runtime/report_cache.py](runtime/report_cache.py) 留**进程内快照**（TTL 6h / 最多 200 份 / 单份 2 万字），`format_referenced_tickets` 命中即注入原文并标注「本次进程内快照，未落库」；拿不到时注入明确口径要求**如实说明「报告不落库，只能重新点一次『帮我分析』」**。测试：`runtime/test_report_cache.py`、`contexts/test_ref_report.py`。

**教训**：**只在注释里写「不要让 LLM 假装看到了它」，等于没写**——约束必须真的进 prompt（诊断侧 #11/#12 是同一族问题的另一端：服务端拦截了 LLM 的话术，就必须由服务端补发修正/成功消息）。

## 2026-10-12 视觉链路全线 404：`Model not exist.`（#8）

**现象**：生产 10-12 起所有 vision 请求 404，`analyze_images` 报 `ServiceUnavailableError`，任务 Agent 降级成「图片未能解析」。

**根因**：`VISION_MODEL` 指的模型**被下线**了。

**教训（一次被日志推翻的判断）**：我先断言「这是一次从没成功过的切换、URL 拼错了」，随即被日志推翻——40 次 vision 请求全部使用该模型名，其中 09-16 / 09-18 / 09-20 / 09-29 共 **37 次成功**，只有 10-12 才 404。**「配置看起来不对」推不出「从没成功过」，日志能给出反证。**

**修复**：`.env` 改 `VISION_MODEL=qwen3-vl-plus`（**不入库**），用真实截图端到端跑 `analyze_images()` 验证（1 张 23s / 3 张 36s，能准确读出故障码与外设表）。选型靠实测：该账号 `/v1/models` 只列 3 个 VL 模型，`qwen3-vl-plus` 能看图，`qwen-vl-ocr` 只回纯 OCR 文本（无画面理解）。

## 2026-10-12 死代码清理与文档同步（#9）

净 **−147 行**（18 文件，`68c61bcd`）：`DISCUSS_*_KEYWORDS`/`TEXT_ATTACH_EXTS`/`query_matches`、`MAX_ROUNDS_PER_TASK`/`SupervisorError`、`TodoList.next_pending/all_done`、`AiTaskAgent._save_analysis_context/_index_solution`（含随之只写的 `self._memory`）、`_TS_JSDOC_RE`、`_split_level_body`/`LogIndex.count_in_window`、`SemanticCodeIndex.position_of`、`ManualGuide.relevant_signals`、`get_resolution_worker`，以及 12 个未用 import（用 Pylance `source.unusedImports` 做的）。

**注意**：删 `_index_solution` **不是功能回归**——方案入库的闭环由 `ai/core/knowledge_worker.py` → `solution_sink.process_ticket` → `retriever.index_task_resolution` 承担，仍然活着。

**教训**：删之前先确认「是不是有另一条路承担了同一职责」，否则会把唯一实现删掉。

## 2026-10-12 配额从未生效：`max_usage_per_session` 是死代码（#10）

**现象**：`project_info` 与 `log_analyze` 都声明了 `max_usage_per_session = 3`（[capabilities/tools/project_info.py:395](capabilities/tools/project_info.py)、`capabilities/log_analyzer/log_analyze.py:58`），看起来是个硬上限。

**根因**：计数器 `_usage_count` 曾是**能力实例**的属性（[capabilities/core/base.py](capabilities/core/base.py)，本次已删），而**没有任何一条取用路径复用实例**——[capabilities/core/registry.py](capabilities/core/registry.py) 的 `get()` 明确「每次新建」（`cap_cls()`）；supervisor 每次派发前都重新 `get`（[capabilities/core/supervisor.py](capabilities/core/supervisor.py) 的 `_run_one`）；`discuss_flow` 的兜底补派也一样（[handlers/discuss_flow.py](handlers/discuss_flow.py) 的 `_ask_reanalyze_log`）。实例用完即弃 → `_claim_usage()` 每次看到的 `_usage_count` 都是 0 → **上限永远达不到**。

实测（同一份代码）：

```
复用同一个实例        -> [True, True, True, False, False]   # 机制本身是好的
真实的调用路径        -> [True, True, True, True, True]     # 永远不拦
```

`git log -S max_usage_per_session` 显示它随首个公测版本 `f3cd531f`（2026-08-23）进来，**至今没有生效过**。全仓也没有任何测试提到配额。

**为什么一直没被发现**：唯一可能造成实例复用的钩子 `_instantiate` 从来没有被任何能力实现过（registry 的 docstring 里写着这件事，那个死分支已被删掉）；而「配额」这种机制**不生效时是静默的**——没有报错、没有日志，只是那个上限不存在而已。

**遗留的错误描述（已一并更正）**：

- [待做.md](待做.md) §7 写「同一轮最多调 3 次」并标为已落地——既不是「同一轮」，也**根本没生效**，已移回「还没做」并写明原因（本次已真收口，见下）；
- `registry.py` 的 docstring 至今宣称有 `get_singleton()` 复用单例，**该方法全仓不存在**，已改成警告。

**已修复（真收口）**：机制下沉到 supervisor 的机制层——

- 新增 [capabilities/core/usage.py](capabilities/core/usage.py)：台账挂 `runtime_ctx`，**按请求**计数（不再挂实例）；
- `registry.limit_of(name)` 直读类属性，不实例化能力；
- 三道拦：`_guard_decision` 把超出上限的计划步骤**直接不排**；`_dispatch_group` 派发前 `claim`，用满就**不执行**；`run()` 把已用满的能力从可选集里 `drop_exhausted` 摘掉；
- 被拦下时写一条**明确指令**进汇总正文（`_synthesize(results, notes)`），让整理答复的模型给出**有界**结论，而不是以为还有更深的分析；
- 删掉 base.py 里那套挂在实例上的 `_usage_count` / `_claim_usage`（它不可能生效），并把「配额由谁管」写进注释，防止后人再挂回实例上。

测试 [capabilities/core/test_usage_quota.py](capabilities/core/test_usage_quota.py) 7 例，其中一例专门锁「换个实例不能让计数归零」这条回归；全量 `862 passed, 27 skipped`。真实能力实测：`project_info`（上限 3）→ `[True, True, True, False]`，`drop_exhausted` 只摘它一个，说明进得了最终正文。

**边界（有意）**：只管 **LLM 自主调度** 的次数。`discuss_flow` 里代码确定性的兜底调用（按用户要求补做一次日志分析）不计入——那是产品承诺，不该被配额吃掉。

**教训（本条最值钱）**：把软约束写成代码，不等于它就是硬的。**声明一个上限，必须同时有一个测试能证明它会拦**——否则它只是一句写错位置的注释。诊断侧的同类教训是反方向的：prompt 里的软约束换个模型就失效，最终靠**物理移除工具**才收口（见 [../AiDiagnosisPlatform/issues.md](../AiDiagnosisPlatform/issues.md) 的「死磕保险」）。

## 工具链与协作约定（#11–#14，全是本仓真实踩过的）

| # | 坑 | 现象 | 正确做法 |
|---|----|------|----------|
| 11 | PowerShell 5.1 按 **ANSI** 读 `.ps1` | UTF-8 无 BOM + 中文的脚本解析失败（`sync.ps1` 直接语法错） | `.ps1` 必须带 UTF-8 BOM（`d18b052a`）。本仓 `daily-sync-test.ps1`、`scripts/setup_mysql.ps1` 本来就有 BOM；`automation/scripts/*.ps1` 纯 ASCII 无风险 |
| 12 | `2>&1` + `$ErrorActionPreference='Stop'` | git 的 stderr 进度被 PS 包成 `NativeCommandError` → 脚本中止。我曾据此**误判**脚本有第二个 bug，撤回了 | 别在启用 Stop 的脚本里给 git 加 `2>&1` |
| 13 | `git diff --cached --name-only` 对中文名转义加引号 | 校验「暂存集是否只有我的文件」会看错 | 用 `git -c core.quotepath=false` |
| 14 | `Out-File -Encoding utf8`（PS 5.1）会写 BOM | 提交信息的**主题行开头多一个** `` | 用 `[System.IO.File]::WriteAllText($p, $msg, (New-Object System.Text.UTF8Encoding($false)))` |

**提交纪律**：工作区经常有**别人未提交的活**（本项目同时有多人在同一分支作业）。提交时**只显式 `git add` 自己的文件**，绝不 `-A`；提交前后都用第 13 条校验暂存集。

**推送**：`.agents/skills/smart-dispatch-git/scripts/sync.ps1` 的「脏工作区」守卫是为 merge 设计的，别人有未提交改动时会挡住 `-Action push`。**push 不碰工作区**，可直接按技能文档的等价命令 `git push origin ai-feature-dispatch`（禁止 `--force`）。

## 排查方法教训：静态扫描的假阳性（#15）

一次「文档标识符/路径是否还存在」的自动化核查，出现过这些假阳性，**都靠回代码抽样核对才发现**：

- 只拿 `.py` 当语料 → 误报 30 个「文档里的标识符不存在」，实为**前端组件名 / DDL 列名 / 配置键**；
- 路径检查误报文档**故意**引用的「不存在的文件」（那正是它在说的事）；
- `test_*` 函数必然显示为零引用；
- `_CallVisitor.visit_Call` 由 `ast.NodeVisitor` **动态派发**，静态找 `visit_Call` 调用点必然找不到；
- `from __future__ import annotations` 是编译指令，不是未用 import。

**教训**：扫描结果只是**候选集**，下结论前必须回代码核对；说「这个不存在 / 没人用」之前，先问「我的语料是不是漏了一整个语言/目录」。

## 测试基建债（记录，暂不修）

- `ai/requirements.txt` 曾**漏声明 `pytest-asyncio`**（已补）。本仓跑测试前先确认 venv 是 `D:\CodeHub\AI\OpenRobotService\.venv\Scripts\python.exe`。
- 全量首跑出现过 `3 failed, 802 passed`：1 个是真 bug（缓存读取错位）、2 个是过时测试。**判断是不是既有问题要用 `git stash` 做基线**，不要凭印象（本次就是这么定的性）。
- 用户口径：**临时/一次性测试文件留在本地不提交**，只提交代码与长期测试。

---

## 跨平台 / 真实位置在别处的条目（教训通用，别搞错归属）

### 缓存读写契约错位 + 双人反向修复 → 坏合并（#16）

**真实位置**：`ai/agents/AiDiagnosisPlatform/pipeline.py`（`_ALL_PROJECT_NAMES_CACHE` :29/:2407/:2433，`_RECENT_TICKET_PROJECTS_CACHE` :32/:2549/:2586，`_plan_tools` :3294）——**不在 AiTaskPlatform**。

**问题**：写 `_ALL_PROJECT_NAMES_CACHE[:] = [(now + 300, names)]`（单条目列表里装 tuple），读 `now < _ALL_PROJECT_NAMES_CACHE[0]` → `float < tuple` 抛 `TypeError`。比较先于过期判断就抛，**坏条目永不过期**：首次填充后一直崩到进程重启。异常被 `_plan_tools` 的 `except` 吞掉，后果是**意图路由丢失、项目预填失效**（返回兜底的 `"diagnosis"`）。

我曾在提交信息里把它描述成「5 分钟内每次调用都抛」——比实际**轻**了，口径已更正为「首次填充后一直崩到进程重启」。

**坏合并（本条最该记住的部分）**：我改**读取**（`[0][0]`/`[0][1]`），同事改**写入**（`[now+300, names]` 平铺），两人方向相反。git **自动合并成功、无冲突标记**，但产出「平铺写入 + 嵌套读取」的坏混种 → `TypeError: 'float' object is not subscriptable`。

**教训**：合并后的检查点不是「有没有冲突标记」，而是**「不变量还成不成立」**。修同一个 bug 时，先看对方改成什么口径，别各修一半。

### 作者名的第二份实现（#17）

**真实位置**：`ai/agents/AiDiagnosisPlatform/pipeline.py:949` 的 `_lookup_ticket_ref`（客服/诊断入口），与 AiTaskPlatform 的 `contexts/comments.py`（讨论区/`@#` 引用）**是同一件事的两份独立实现**，同一类 `created_by_name` 问题两边都有（`5a908e82` + `ai/tests/test_ticket_ref_comment_authors.py`）。

### 数据库迁移与模型必须成对（#18）

**现象**：`sqlalchemy.exc.OperationalError (1054, "Unknown column 'tasks.archived_by'")`。

**根因**：提交 `9726d417` 改了模型却没写 alembic 迁移（提交信息里就写着 "migration excluded per user request"）。

**修复**：手工加列 + 索引 + 追加枚举值（枚举值**追加在末尾**，零数据重写），脚本幂等、重跑全 SKIP。

**教训**：模型变更与迁移**必须成对**进仓，否则生产第一次查询就炸。

### 基础设施：Qdrant 502（#19）

**现象**：`外部服务不通: qdrant — 列出集合失败: 502 (Bad Gateway)`。

**我先给的判断是错的**（怀疑 `HTTP_PROXY` 劫持 localhost），实测推翻：真因是 **Qdrant 容器 4 天前就退出了**（`Exited(255)`、`RestartPolicy=no`）。

**修复**：`docker start qdrant` + `--restart unless-stopped`，之后 `validate_ai_config()` 三项全绿。

**教训**：502 先查**依赖进程是否活着**，再怀疑代理/网络这类「听起来更聪明」的原因。

## 刻意不共用的决策（记下来，免得后人当 bug 修掉）

- **AiTaskPlatform 与 AiDiagnosisPlatform 的评论作者名映射不抽成共用**：两个 Agent 零交叉导入，各留一份。理由是诊断入口后续可能要对人物信息**脱敏**，`umap` 就是脱敏注入点；此时共用反而互相牵制（用户 2026-10-12 拍板）。
