# 项目扩展信息（ext_info）与项目信息树（info_nodes）后端接口变更说明

> 变更日期：2026-09-14
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

- 模板目录：[project_templates/](file:///d:/CODE/9_9/OpenRobotService/backend/app/config/project_templates)，当前提供 `default.yaml`（12 个根节点、共 69 个节点，对齐演示数据 a.json 的标准信息树）。
- 选择规则：按项目的 `project_type` 找 `{type}.yaml`，文件不存在则回退 `default.yaml`；新增模板只需加文件，无需改代码。
- 加载与缓存：模板在 Service 层集中加载，进程内按类型缓存，每次返回**深拷贝**避免调用方污染缓存。
- 模板分三段：
  - `overview` → `ext_info.overview`（progress / tags / wecom_id / ai_summary 等标量）
  - `activity` → `ext_info.activity`（version_changes / stage_changes 事件数组）
  - `info_nodes` → 递归树**定义**，仅含 title / sort_order / children，**不含 id / value**

实现：[project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py#L18-L52) 中的 `_get_ext_info_template()` 与 `_split_template()`。

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

### 4.4 Schema 变更汇总

- `ProjectBase`：新增 `ext_info: Optional[Dict[str, Any]]`
- `ProjectUpdate`：新增 `ext_info`、`version: Optional[int]`
- `ProjectResponse`：新增 `ext_info`、`version: int = 1`

## 五、项目信息树接口（新增，6 个）

路由文件：[info_nodes.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/info_nodes.py)
Service：[info_node_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_service.py)
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

## 六、并发与一致性小结

1. **ext_info 并发编辑**：靠 `version` 乐观锁（冲突 409）+ 更新瞬间行锁串行化；内部系统写入不带 version，显式绕过乐观锁。
2. **信息树并发编辑**：逐节点独立 CRUD，天然缩小冲突粒度；移动/删除不做跨节点协同锁，后写覆盖先写。
3. **创建初始化的一致性**：project 插入与 info_nodes 初始化在同一 Session 内分两次 commit；模板实例化每节点生成独立 UUID，保证同源模板的多个项目不发生主键冲突。
4. **老数据兼容**：`ext_info` 为 NULL 的存量项目读取时按模板 lazy 填充（仅响应、不落库）；`version` 为 NULL 时按 1 参与校验与自增。

## 七、HTTP 错误码汇总

| 状态码 | 场景 | 来源 |
|--------|------|------|
| 400 | 更新节点时无任何有效字段；授权接口 type 参数非法 | info-nodes / licenses |
| 401 | 未提供/无效 token、token 缺用户信息（/me 类接口） | projects |
| 404 | 项目/节点不存在（含软删除项目） | projects、info-nodes |
| 409 | 项目编号/名称重复；**乐观锁版本冲突** | projects（PUT） |
| 500 | 权限服务联动失败、删除项目外键残留等 | projects |

## 八、涉及文件清单

| 类型 | 文件 |
|------|------|
| 迁移 | [a7b8c9d0e1f2_add_project_ext_info.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/a7b8c9d0e1f2_add_project_ext_info.py)、[b2c3d4e5f6a7_add_project_info_node.py](file:///d:/CODE/9_9/OpenRobotService/backend/alembic/versions/b2c3d4e5f6a7_add_project_info_node.py) |
| 模型 | [app/models/delivery.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/models/delivery.py)（Project / ProjectInfoNode）、[models_das/models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/models_das/models.py)（再导出） |
| Schema | [schemas_das/request_models.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/schemas_das/request_models.py) |
| API | [api/projects.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/projects.py)、[api/info_nodes.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/api/info_nodes.py) |
| Service | [services/project_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/project_service.py)、[services/info_node_service.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/services/info_node_service.py) |
| 模板 | [config/project_templates/default.yaml](file:///d:/CODE/9_9/OpenRobotService/backend/app/config/project_templates/default.yaml) |
| 路由挂载 | [modules/admin/__init__.py](file:///d:/CODE/9_9/OpenRobotService/backend/app/modules/admin/__init__.py) |

## 九、前端对接要点

1. 打开项目详情时保存响应中的 `version`；编辑保存（PUT）原样带回，收到 409 提示刷新重试。
2. `ext_info` 按整体对象提交；信息大纲树不要放进 `ext_info`，改用 `/info-nodes/*` 逐节点操作。
3. 新建节点前由前端生成 UUID 作为 `id`；删除节点会连带删除整棵子树，需二次确认。
4. 拖拽节点后调 PATCH move；批量替换整树调 import（注意会先清空旧树）。
