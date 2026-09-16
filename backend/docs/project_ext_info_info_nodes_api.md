# 项目扩展信息（ext_info）与项目信息树（info_nodes）后端接口变更说明

> 变更日期：2026-09-14（初版）；2026-09-15 新增 5.8 文件识别接口；2026-09-16 新增 4.4 AI 项目摘要接口
> 模块：`app/modules/admin`（后台管理）
> 路由公共前缀：`/api/admin`（`/api` 来自 `API_V1_STR`，`/admin` 来自 `admin_router`）

## 一、功能概述

本次为支撑「项目信息页」改造，后端做了三项变更：

| # | 变更 | 存储位置 | 目的 |
|---|------|----------|------|
| 1 | `project` 表新增 `ext_info` JSON 列 | 主表 | 承接递归嵌套、变化频繁的展示型字段（overview / activity） |
| 2 | `project` 表新增 `version` 乐观锁列 | 主表 | 防止整文档读改写模式下多人并发编辑互相覆盖 |
| 3 | 新增 `project_info_node` 表及 6 个接口 | 独立子表 | 用户可自由增删/拖拽/编辑的信息大纲树，逐节点 CRUD 互不干扰 |

**职责边界（重要）**

- `ext_info` 存「开发者定义结构的值」——结构固定、面向展示；创建时由 YAML 模板初始化。
- `project_info_node` 存「结构本身就是用户数据」——节点可由用户自由增删、拖拽排序、编辑值，每个节点独立写入，不会因整树读改写而丢更新。
- `info_nodes` 数据**不放在** `ext_info` 中，模板里的 `info_nodes` 段在项目创建时被拆分写入子表。

## 二、数据库变更

### 2.1 迁移脚本

| 迁移版本 | 日期 | 内容 |
|----------|------|------|
| `a7b8c9d0e1f2` | 2026-09-09 | `project` 表增加 `ext_info`、`version` 两列 |
| `b2c3d4e5f6a7` | 2026-09-14 | 新建 `project_info_node` 表（依赖前一迁移） |

### 2.2 project 表新增列

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `ext_info` | JSON | NULL | 项目扩展信息，递归嵌套字典/数组；仅存 `overview` + `activity` |
| `version` | INT | NOT NULL DEFAULT 1 | 乐观锁版本号，每次更新成功自增 1 |

ORM 定义：[delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py#L147-L153)（`Project` 类，JSON 列由 ORM 自动反序列化为 dict）。

### 2.3 project_info_node 新表

邻接表（adjacency list）模型：

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| `id` | VARCHAR(64) | PK | 节点 UUID（创建节点时由客户端生成；模板实例化时服务端生成） |
| `project_id` | VARCHAR(64) | NOT NULL | 所属项目 ID（= project.id / project_code） |
| `parent_id` | VARCHAR(64) | NULL | 父节点 ID，NULL 表示根节点 |
| `title` | VARCHAR(255) | NOT NULL | 节点标题 |
| `content_type` | VARCHAR(32) | NOT NULL DEFAULT 'text' | 内容类型：text/image/file/... |
| `value` | TEXT | NULL | 节点值 |
| `sort_order` | INT | NOT NULL DEFAULT 0 | 同级排序 |
| `created_at` / `updated_at` | VARCHAR(30) | NOT NULL | 字符串时间戳，格式 `YYYY-MM-DD HH:MM:SS` |

索引：

- `idx_pn_project (project_id)` —— 按项目查树
- `idx_pn_parent (parent_id)` —— 按父节点查子级
- `idx_pn_project_parent_sort (project_id, parent_id, sort_order)` —— 同级有序查询

ORM 定义：[delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py#L294-L322)（`ProjectInfoNode` 类，经 `models_das/models.py` 再导出）。

## 三、YAML 模板初始化机制

- 模板目录：[project_templates/](file:///d:/CODE/9_9/OpenRobotService/backend/app/config/project_templates)，当前提供 `default.yaml`（13 个根节点、共 121 个节点，对齐《项目信息树形图》）。
- 选择规则：按项目的 `project_type` 找 `{type}.yaml`，文件不存在则回退 `default.yaml`；新增模板只需加文件，无需改代码。
- 加载与缓存：模板在 Service 层集中加载，进程内按类型缓存，每次返回**深拷贝**避免调用方污染缓存。
- **容错**：模板解析失败（缩进/编码错误）只记 error 日志并按空模板处理，不让项目接口整体 500（`ext_info` 为 NULL 的存量项目读取时也会走模板）。
- 模板分三段：
  - `overview` → `ext_info.overview`（progress / tags / wecom_id / ai_summary 等标量）
  - `activity` → `ext_info.activity`（version_changes / stage_changes 事件数组）
  - `info_nodes` → 递归树**定义**，节点字段：

| 字段 | 说明 |
|------|------|
| `title` | 节点标题（必填） |
| `sort_order` | 同级排序，缺省按书写顺序 |
| `content_type` | `text`（默认）/ `select` / `file` / `image`；有 `options` 时缺省即 `select` |
| `options` | 仅 `select` 用；实例化时写入 `value = {"selected":"","options":[...]}`（与前端下拉解码一致） |
| `value` | 预置值（可选）：`text` 用字符串，其余按 `content_type` 的结构 |
| `children` | 子节点（递归） |

- 模板结构有两条硬约束，改 YAML 时必须满足（前端 UI 的既定行为）：
  1. **最深 4 层**（`PROJECT_INFO_MAX_DEPTH = 4`，第 4 层不可再挂子节点）；
  2. **`select` 节点必须是末级**（前端只对叶子节点渲染内容编辑器）。
  - 思维导图里第 5 层的「可选值清单」因此统一表达为 `content_type: select` + `options`，不展开成子节点。
- **区域联动**（仅前端渲染行为，接口与数据不变）：`基础信息 → 项目区域/地点` 下，`区域选项` 这个下拉含 `大陆(China Mainland)` 选项。前端据此联动——选「大陆」时显示 `省份`/`地区`，选其它非空区域时显示 `具体国家`，未选择时三者都不显示；节点始终在数据里（切回时原值还在），只是隐藏渲染。`省份`/`地区`/`具体国家` 三个标题不能改名，否则联动失效（实现见前端 `projectInfoTree.ts` 的 `isInfoNodeVisible`）。旧版模板初始化过的项目若缺 `具体国家` 节点，需补一个同级节点才能生效。

实现：[project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py) 中的 `_get_ext_info_template()` / `_split_template()` / `get_info_nodes_template()` / `template_node_value()`。

## 四、项目接口的修改

路由文件：[projects.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/projects.py)
Service：[project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py)
请求/响应模型：[request_models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/schemas_das/request_models.py)

### 4.1 响应体新增字段（影响所有项目查询接口）

以下接口返回的每个项目对象均新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ext_info` | object \| null | 扩展信息；**库中为空（迁移前的老项目）时，读取时自动用模板的 overview+activity 填充返回，但不写回数据库**，直到下次保存才持久化 |
| `version` | int | 乐观锁版本号，老数据为 NULL 时按 1 返回 |

涉及接口（路径与行为不变，仅响应体扩充）：

- `GET /api/admin/projects/` 项目列表
- `GET /api/admin/projects/me` 当前用户关联项目
- `GET /api/admin/projects/{project_id}` 项目详情

Service 逻辑（`_convert_to_dict`）：序列化主表列后附加两字段；`ext_info` 为空时调用 `_split_template(project_type)` 取模板部分（只取 overview+activity，info_nodes 走独立表不混入）。

### 4.2 POST /api/admin/projects/ —— 创建项目（行为增强）

请求体 `ProjectCreate` 新增可选字段 `ext_info`。

Service 逻辑（`create_project`）：

1. 常规字段处理：project_code → code/id 映射、JSON 字段（field_links / stage_notes / project_documents / system_integration）序列化、白名单过滤表列。
2. 按 `project_type` 拆分模板：
   - 请求未传 `ext_info` 时，用模板的 overview+activity 初始化；显式传入则以传入值为准。
   - `info_nodes` 树**总是**从模板生成，不受请求体影响。
3. 插入 project 行并提交。
4. 递归实例化信息树：为模板每个节点生成**全新 UUID**（不同项目同模板不撞主键），value 留空，保持模板的 title / sort_order / 层级，批量写入 `project_info_node`，再次提交。

冲突响应（沿用唯一性校验）：项目编号或名称已存在返回 `409`，detail 为「项目编号「xx」已存在，请重新输入」等；并发提交撞唯一约束同样兜底为 409。

### 4.3 PUT /api/admin/projects/{project_id} —— 更新项目（新增乐观锁）

请求体 `ProjectUpdate` 新增：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ext_info` | object | 否 | 扩展信息整体提交（整文档读改写） |
| `version` | int | 否 | 编辑前从详情接口读到的版本号；**前端编辑提交必须带回** |

Service 逻辑（`update_project`）：

1. 从更新数据中取出 `version`（不参与字段赋值）。
2. `SELECT ... FOR UPDATE` 行锁锁定该项目行（过滤软删除），串行化同项目并发更新；项目不存在返回 None → API 层 404。
3. **版本校验**：请求带了 version 且与库中当前值（NULL 视为 1）不一致 → 回滚释放行锁，抛 `ProjectConflictError`，API 层转为 `409`，detail 形如「项目已被他人修改（当前版本 3，提交版本 2），请刷新后重试」。
4. 请求**不带** version 时跳过校验，保持旧行为（供内部系统更新使用，如企业微信同步）。
5. 应用字段更新（JSON 字段空值置 NULL、白名单过滤），随后 `version = 当前值 + 1`，提交并返回最新项目（含新 version）。

前端处理约定：捕获 409 后提示用户刷新页面、基于最新数据重新编辑，不得静默重试覆盖他人修改。

### 4.4 POST /api/admin/projects/{project_id}/ai-summary —— AI 项目摘要（新增）

- 用途：后台管理-项目详情页「项目概况」卡底部「AI 项目摘要」卡片；点「AI 生成 / 重新生成」时，后端读取项目基础字段 +「项目信息管理」整棵信息树，由大模型总结项目基础情况，写回 `ext_info.overview.ai_summary` 并随响应返回。
- 请求：无 body。
- Service 逻辑（`project_ai_summary_service.generate_for_project`）：
  1. 读该项目的完整信息树（同 5.1），为空直接 400（提示先初始化信息树）；
  2. 组装提示词（纯函数 `build_summary_prompt`）：项目 25 个已入库基础字段按中文标签逐行输出（空值跳过）+ 信息树按「父路径 / 子节点：内容」逐行渲染——text 折叠空白、select 解 `{"selected":...}`（非 JSON 旧数据按原文兜底）、file/image 取文件名；**空值节点不输出正文**，只统计为「（另有 N 个末级节点未填写）」附在末尾；正文超 12,000 字符截断；
  3. 调大模型：接口用仓库根 `ai/core/llm.py` 的 `LLMClient`，密钥/模型取 backend 配置（`settings.LLM_API_KEY` / `LLM_API_URL` / `LLM_MODEL_NAME`，即**与文件识别（5.8）同一个 DeepSeek flash**）；temperature=0.3、max_tokens=1500、非流式、显式关闭思考链；
  4. 清洗输出（去 ``` 围栏/首尾引号）后深拷贝 `ext_info` 合并 `overview.ai_summary`，走 `project_service.update_project` 落库（内部写入**不带 version**、跳过乐观锁，version 仍 +1，与企微同步同约定）；保存失败（项目被删）抛 400。
- 响应 `200`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `summary` | string | 生成的摘要正文（结构化 Markdown：`## 小节` + `- 要点`，250 字以内） |
| `model` | string | 实际使用的模型名 |
| `ext_info` | object | 写库后的完整 ext_info，前端直接替换本地状态即可，无需重新拉详情 |

- 错误：`400`（信息树暂无节点 / 保存时项目已被删除）；`404`（项目不存在）；`503`（`LLM_API_KEY` 未配置、ai 模块缺失或大模型调用失败，detail 带中文原因）。
- 提示词由两部分固定文案 + 动态资料组成：system「只输出总结本身（无代码块围栏/解释/寒暄）」；user「【项目基础字段】+【项目信息管理】+【输出格式】（结构化 Markdown：固定小节顺序——项目概况/硬件与车型/系统与部署/交付与进度/风险与关注点，无资料的小节省略；每节 1～2 条要点、全文 250 字以内、加粗最多 3～4 处；**不得编造**、不重复、不用套话）」。前端用 react-markdown 渲染该 Markdown。

### 4.5 Schema 变更汇总

- `ProjectBase`：新增 `ext_info: Optional[Dict[str, Any]]`
- `ProjectUpdate`：新增 `ext_info`、`version: Optional[int]`
- `ProjectResponse`：新增 `ext_info`、`version: int = 1`

## 五、项目信息树接口（新增，8 个）

路由文件：[info_nodes.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/info_nodes.py)
Service：[info_node_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_service.py)、[info_node_import_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_import_service.py)（仅 5.8）
路由前缀：`/api/admin/info-nodes`，tag：`admin-info-nodes`

> 鉴权现状：本组路由暂未挂载 `security` 依赖（与项目接口的 DEBUG 开关鉴权不同），当前依赖部署侧网关管控，后续如需端级鉴权再补充。

节点对象标准字段：`id, project_id, parent_id, title, content_type, value, sort_order, created_at, updated_at`；树查询时每节点额外含 `children` 数组。

### 5.1 GET /info-nodes/projects/{project_id} —— 获取信息树

- 功能：返回项目完整信息树的递归嵌套结构。
- Service 逻辑：一次查出该项目全部节点并按 `sort_order` 排序；在 Python 内构建 `parent_id → 子节点` 映射，从 `parent_id IS NULL` 递归组装 children；空树返回 `[]`。项目节点量级为百级，不使用递归 CTE 以兼容 MySQL 版本。
- 响应：`200`，节点数组（根节点列表），每节点含 `children`。

### 5.2 POST /info-nodes/projects/{project_id} —— 创建节点

- 状态码：`201`
- 请求体 `InfoNodeCreate`：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `id` | string | 是 | —— | 客户端生成的 UUID，供后续稳定引用 |
| `parent_id` | string \| null | 否 | null | 父节点 ID，null 为根节点 |
| `title` | string | 否 | "未命名节点" | 标题 |
| `content_type` | string | 否 | "text" | 内容类型 |
| `value` | string \| null | 否 | null | 节点值 |
| `sort_order` | int | 否 | 0 | 同级排序 |

- Service 逻辑：补齐两个时间戳字符串，直接插入单行后 refresh 返回。当前不校验 parent_id 是否存在/同项目，由调用方保证。

### 5.3 PUT /info-nodes/nodes/{node_id} —— 更新节点

- 请求体 `InfoNodeUpdate`：`title` / `content_type` / `value` / `sort_order` 均可选；**不能改 parent_id**（换父请用 move 接口）。
- 业务规则：请求体剔除值为 None 的字段后若为空 → `400 {detail: "无更新字段"}`。
- Service 逻辑：按 id 查节点，不存在返回 None → `404 {detail: "节点不存在"}`；仅对白名单四字段逐个赋值，刷新 `updated_at`，提交返回更新后节点。

### 5.4 PATCH /info-nodes/nodes/{node_id}/move —— 移动/排序节点

- 请求体 `InfoNodeMove`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `new_parent_id` | string \| null | 是 | 目标父节点，null 表示移到根 |
| `new_sort_order` | int | 是 | 目标同级位置，默认 0 |

- 功能：拖拽排序/换父节点。仅更新 parent_id、sort_order、updated_at；节点不存在 → 404。
- 已知约束：服务端**不做环检测**（拖入自身子树的防护由前端树形控件保证）。

### 5.5 DELETE /info-nodes/nodes/{node_id} —— 删除节点（含整棵子树）

- Service 逻辑：先用**递归 CTE**（`WITH RECURSIVE`，MySQL 8）查出该节点及全部后代 ID；结果为空（节点不存在）→ 404；否则按 ID 集合批量删除并提交。
- 响应：`200 {"detail": "已删除节点及其子树"}`。

### 5.6 POST /info-nodes/projects/{project_id}/import —— 批量导入信息树

- 请求体 `InfoNodeImport`：`{"nodes": [ {id, title, content_type?, value?, sort_order?, children?: [...]} ]}`，递归嵌套。
- Service 逻辑（同一事务内）：
  1. 先物理删除该项目下的全部旧节点（替换式导入）；
  2. 递归展平入参为行列表（parent_id 在展平过程中按层级挂上，缺省值同创建）；
  3. `bulk_save_objects` 批量插入并提交。
- 响应：`200 {"imported": <节点总数>}`。
- 适用场景：从 a.json 等外部信息树整体迁入。

### 5.7 POST /info-nodes/projects/{project_id}/import-template —— 按项目模板重建信息树

- 请求体：无（项目 id 走路径参数）。
- 功能：读该项目 `project_type` 对应的 YAML 模板（缺省 `default.yaml`），实例化整棵信息树并**替换**该项目现有全部节点（与新建项目的初始化同一份模板定义）。
- Service 逻辑：查项目（软删除项目视为不存在 → `404`）→ `get_info_nodes_template(project_type)` 取模板 → 递归生成 UUID 与 `content_type` / `value`（`options` 编码为 `{"selected":"","options":[...]}`）→ 复用 `import_tree`（先清空后批量插入）。
- 响应：`200 {"imported": <节点总数>}`；模板为空或解析失败时**不改动现有节点**，返回 `{"imported": 0}`。
- 适用场景：功能上线前创建、信息树为空的存量项目一键初始化；前端信息编辑页空态的「按预设模板初始化」按钮。

### 5.8 POST /info-nodes/projects/{project_id}/parse-file —— AI 识别导入文件（预览，**不落库**）

- 请求：`multipart/form-data`，字段 `file`（单个文件，≤10MB）。
- 支持格式与抽取方式（全部在后端完成，前端不引解析库）：

| 扩展名 | 抽取方式 |
|--------|----------|
| `.docx` | 标准库 `zipfile` 读 `word/document.xml`，段落逐行、表格行转 `单元格 \| 单元格` |
| `.xlsx` | `openpyxl`（read_only），每个工作表以 `# 工作表：名字` 分隔、制表符分列 |
| `.md` / `.markdown` / `.txt` / `.csv` | 按 `utf-8-sig → gbk → utf-8(replace)` 解码 |
| `.doc` / `.xls` | 明确拒绝，提示另存为 `.docx` / `.xlsx` |

- Service 逻辑（`info_node_import_service.analyze_import_file`）：
  1. 校验大小/格式并抽取正文；正文超过 100,000 字符截断（响应 `truncated=true`）；
  2. 读该项目信息树，展平为「节点目录」（每行 `路径<TAB>类型[<TAB>(末级)][<TAB>可选项：a|b]`）——只把目录与文件正文放进 prompt，**不要求大模型输出整树**；
  3. 调大模型（与「摇人」共用 `settings.LLM_API_KEY` / `LLM_API_URL` / `LLM_MODEL_NAME`，即 DeepSeek flash；temperature=0.2、超时 120s、非流式），要求只输出 JSON `{"items":[{"title","value","nodeTitle","suggestedParentPath"}]}`；prompt 要求「把握 ≥ 0.9 才填 nodeTitle，否则给 suggestedParentPath」「select 节点 value 必须命中可选项，否则按未匹配」；
  4. 解析返回（容忍 ```json 围栏与前后杂文字）后由**后端做权威匹配**（大模型的 nodeTitle 仅作提示）：
     - 节点标题/完整路径去空白标点后精确匹配优先，`difflib.SequenceMatcher` 相似度 **≥ 0.9（满分 1）** 兜底模糊匹配；同名节点用 `suggestedParentPath` 消歧；
     - select 节点 value 未命中可选项 → 降级为未匹配；同节点多条去重；识别值与节点现值一致则跳过；
  5. 分桶返回三类：节点现值为空 → `fill`；非空且与识别值不同 → `overwrite`；无匹配节点 → `unmatched`（`suggestedParentPath` 逐段解析为 `suggested_parent_id`，层级上提到 ≤4 层，解析不到则 null，由前端用「导入信息」根兜底）。
- 响应 `200`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_name` / `model` | string | 文件名 / 实际使用的模型名 |
| `text_length` / `truncated` / `extracted` | int / bool / int | 抽取字符数 / 是否截断 / 大模型识别条目数 |
| `fill` / `overwrite` | 数组 | 元素：`node_id, path, title, content_type, current, value`（`current` 为空串=将填写） |
| `unmatched` | 数组 | 元素：`title, value, suggested_parent_id, suggested_parent_path` |

- 错误：`400`（格式不支持/内容为空/项目无数节点）；`503`（`LLM_API_KEY` 未配置或大模型调用失败，detail 带中文原因）。
- **本接口不写库**：前端预览勾选后，用 5.3 更新（fill/overwrite）与 5.2 创建（unmatched）逐节点落库；未匹配且无归属的条目挂到按需创建的「导入信息」根节点下。

## 六、并发与一致性小结

1. **ext_info 并发编辑**：靠 `version` 乐观锁（冲突 409）+ 更新瞬间行锁串行化；内部系统写入不带 version，显式绕过乐观锁。
2. **信息树并发编辑**：逐节点独立 CRUD，天然缩小冲突粒度；移动/删除不做跨节点协同锁，后写覆盖先写。
3. **创建初始化的一致性**：project 插入与 info_nodes 初始化在同一 Session 内分两次 commit；模板实例化每节点生成独立 UUID，保证同源模板的多个项目不发生主键冲突。
4. **老数据兼容**：`ext_info` 为 NULL 的存量项目读取时按模板 lazy 填充（仅响应、不落库）；`version` 为 NULL 时按 1 参与校验与自增。

## 七、HTTP 错误码汇总

| 状态码 | 场景 | 来源 |
|--------|------|------|
| 400 | 更新节点时无任何有效字段；授权接口 type 参数非法；AI 摘要时信息树无节点 | info-nodes / licenses / projects（ai-summary） |
| 401 | 未提供/无效 token、token 缺用户信息（/me 类接口） | projects |
| 404 | 项目/节点不存在（含软删除项目） | projects、info-nodes |
| 409 | 项目编号/名称重复；**乐观锁版本冲突** | projects（PUT） |
| 500 | 权限服务联动失败、删除项目外键残留等 | projects |
| 503 | 文件识别接口 / AI 项目摘要：`LLM_API_KEY` 未配置或大模型调用失败 | info-nodes（parse-file）、projects（ai-summary） |

## 八、涉及文件清单

| 类型 | 文件 |
|------|------|
| 迁移 | [a7b8c9d0e1f2_add_project_ext_info.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/a7b8c9d0e1f2_add_project_ext_info.py)、[b2c3d4e5f6a7_add_project_info_node.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/b2c3d4e5f6a7_add_project_info_node.py) |
| 模型 | [app/models/delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py)（Project / ProjectInfoNode）、[models_das/models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/models_das/models.py)（再导出） |
| Schema | [schemas_das/request_models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/schemas_das/request_models.py) |
| API | [api/projects.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/projects.py)、[api/info_nodes.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/info_nodes.py) |
| Service | [services/project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py)、[services/info_node_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_service.py)、[services/info_node_import_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_import_service.py)、[services/project_ai_summary_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_ai_summary_service.py)（4.4） |
| 测试 | [tests/test_info_node_import.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_info_node_import.py)（文本抽取/目录与 prompt 构造/LLM 返回解析/0.9 阈值匹配/select 校验/归属解析，13 用例）、[tests/test_project_ai_summary.py](file:///d:/CODE/9_9/OpenRobotService/backend/tests/test_project_ai_summary.py)（节点内容解码/信息树渲染/prompt 组装/输出清洗，10 用例） |
| 模板 | [config/project_templates/default.yaml](file:///d:/CODE/9_9/OpenRobotService/backend/app/config/project_templates/default.yaml) |
| 路由挂载 | [modules/admin/__init__.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/__init__.py) |

## 九、前端对接要点

1. 打开项目详情时保存响应中的 `version`；编辑保存（PUT）原样带回，收到 409 提示刷新重试。
2. `ext_info` 按整体对象提交；信息大纲树不要放进 `ext_info`，改用 `/info-nodes/*` 逐节点操作。
3. 新建节点前由前端生成 UUID 作为 `id`；删除节点会连带删除整棵子树，需二次确认。
4. 拖拽节点后调 PATCH move；批量替换整树调 import（注意会先清空旧树）。
5. 空树项目一键初始化调 `import-template`——模板结构在后端 YAML 里，前端不保留副本（原前端常量 `PROJECT_INFO_TEMPLATE` 已删除），避免两套模板漂移。
6. 导入文件（`/import`）除节点数组外，也接受「标题 → 内容」紧凑映射（`""` 文字、`[...]` 下拉选项、`{...}` 子节点，即 `project_templates/tmp.json` 的写法）。
7. 信息编辑页「文件导入」走 `parse-file`（上传 → 转圈 → 三组预览勾选 → 确认后逐节点 CRUD）；上传时不要手写 `Content-Type`（交给浏览器带 boundary），大模型识别耗时较长，前端请求超时需放宽到 180s 以上。原「JSON 整树导入」入口已被该弹层替换，`/import` 接口与前端 `importInfoTreeApi` 保留未删（截图/调试仍可直调）。
8. 项目概况卡「AI 项目摘要」：非新建模式显示「AI 生成 / 重新生成」按钮（生成中禁用），POST `/projects/{id}/ai-summary`（超时同样放宽到 180s）；摘要正文从 `project.ext_info.overview.ai_summary` 派生并用 react-markdown 渲染（结构化 Markdown；纯文本旧数据也兼容），生成响应里的 `ext_info` 直接替换本地状态即可持久展示；未生成过时展示「暂无数据」，空信息树项目后端会返回 400 提示先初始化信息树。
