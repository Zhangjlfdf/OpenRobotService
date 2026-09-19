# 本地 UI 回归一键运行

## 1. 目标

从干净终端运行一条命令，完成：

```text
构建前端正式产物
-> 建立测试后端和自动化 AI 两条 SSH 隧道
-> 启动本地 Gateway
-> 执行 1 条 UI 完整业务链路
-> 执行 6 条真实测试环境只读 Smoke
-> 生成联合 Allure HTML
-> 启动本地报告服务
```

## 2. 前置条件

- 仓库根目录存在 `.venv`。
- `.venv` 已安装 `automation/pyproject.toml` 依赖。
- Playwright Chromium 已安装。
- `node`、`npm` 可用。
- Allure CLI 已加入 `PATH`。
- 当前机器可以通过 SSH key 登录测试服务器。

安装 Playwright Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 3. 配置

非敏感配置位于：

```text
automation/config/ui_regression.local.yaml
```

包含 SSH 地址、端口、测试账号名、报告目录、报告端口和隧道超时。
默认隧道超时为 60 秒，用于覆盖测试服务器网络抖动。

密码不写入配置文件。脚本按以下顺序读取：

1. 命令行参数。
2. 环境变量。
3. 交互式安全输入。

支持的环境变量：

```text
UI_REGRESSION_SSH_HOST
UI_REGRESSION_SSH_USER
UI_REGRESSION_SSH_PORT
UI_REGRESSION_SSH_KEY
UI_REGRESSION_U1_USERNAME
UI_REGRESSION_U1_PASSWORD
UI_REGRESSION_U2_USERNAME
UI_REGRESSION_U2_PASSWORD
UI_REGRESSION_CLEANUP_USERNAME
UI_REGRESSION_CLEANUP_PASSWORD
```

## 4. 一键运行

```powershell
.\automation\scripts\run-ui-regression.ps1
```

如果密码未通过参数或环境变量提供，脚本会安全提示输入。

跳过前端构建：

```powershell
.\automation\scripts\run-ui-regression.ps1 -SkipFrontendBuild
```

不自动打开浏览器：

```powershell
.\automation\scripts\run-ui-regression.ps1 -NoOpen
```

显式传入本地测试凭据：

```powershell
.\automation\scripts\run-ui-regression.ps1 `
  -U1Password "..." `
  -U2Password "..." `
  -CleanupPassword "..."
```

## 5. 报告

默认输出：

```text
automation/output/allure-results-ui-regression-combined/
automation/output/allure-report-ui-regression-combined/
```

脚本会记录本次启动的报告服务 PID、启动时间和报告目录。

- 连续运行时优先复用同一个报告服务，端口保持不变。
- 旧报告服务异常或配置变化时，先回收旧实例，再启动新实例。
- 只有配置端口被其他非本脚本进程占用时，才自动切换端口并告警。

报告分类：

```text
场景用例
└── UI完整业务链路

单接口用例
└── 真实测试环境 Smoke
```

每个 Smoke 包含：

- 接口结果。
- 响应摘要。
- 断言结果。

UI 每个 S 步骤包含：

- 断言与响应摘要。
- 脱敏后的网络请求。
- 页面截图。

## 6. 失败处理

- 测试失败时仍会尽量生成 Allure 报告。
- 脚本最终返回 pytest 的非零退出码。
- 清理失败只进入报告告警，不覆盖业务链路结论。
- SSH、Gateway 或测试后端不可用时，脚本直接失败，不伪装为业务通过。

## 7. 安全边界

- 只连接测试环境。
- 不连接生产环境。
- Smoke 只使用只读接口。
- 密码、token、Cookie 不写入仓库。
- 报告中的敏感字段继续使用 `[REDACTED]`。
