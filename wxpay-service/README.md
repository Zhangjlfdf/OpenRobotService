# wxpay-service

微信支付独立微服务（Go），为 OpenRobotService 提供 **商家转账** 能力。
对应技术方案：`backend/docs/微信支付接入技术方案.md`（v1.1，奖励记录 + 立即提现形态）。

---

## 1. 职责边界

| 归属 | 内容 |
| --- | --- |
| **本服务（Go）** | 持有商户私钥 / APIv3 密钥；APIv3 请求签名、应答验签、回调验签、AES-GCM 解密、RSA-OAEP 敏感信息加密；接收微信回调并转发给 FastAPI |
| **FastAPI** | 奖励记录 `reward_record`、转账单 `transfer_bill`、回调流水 `wxpay_notify_log` 等全部持久化与业务状态机；**提现三分支决策**；兜底查单 |
| **不做的事** | 不直连数据库、不下发 `package_info` 之外的业务判断、不保存任何明文姓名 |

---

## 2. 与方案的偏差说明（重要）

方案第 3.2 章建议使用官方 SDK `wechatpay-go`。本实现改为 **纯标准库、零第三方依赖**，原因：

1. 官方 Go 示例（发起转账 / 查询 / 撤销）本身采用 `wxpay_utility` 自签方式，**商家转账在 SDK 中没有强类型 Service**，仍需手写请求体与响应体；
2. 签名（SHA256withRSA）、验签、AES-256-GCM、RSA-OAEP 四个环节的标准库实现**全部有单元测试覆盖**（`internal/wxpay/*_test.go`），可自证正确；
3. 无 `go.sum` / 版本漂移风险，代码量小且可完整审计。

如需切回官方 SDK，**仅需替换 `internal/wxpay/` 一个包**（`Client.Do`、`VerifyAndParse`、`EncryptOAEP` 三个入口），上层 `transfer` / `httpsrv` 无需改动。

---

## 3. 目录结构

```
wxpay-service/
├── cmd/server/main.go              启动、优雅退出、日志初始化
├── internal/
│   ├── config/config.go            环境变量配置与校验
│   ├── wxpay/
│   │   ├── keys.go                 PEM 密钥加载（PKCS#1 / PKCS#8 / PKIX）
│   │   ├── sign.go                 请求签名 + 应答验签
│   │   ├── cipher.go               AES-256-GCM 解密 / RSA-OAEP 加密
│   │   ├── verifier.go             KeyProvider（微信支付公钥 / 平台证书）
│   │   ├── client.go               APIv3 HTTP 客户端（自动签名验签）
│   │   └── notify.go               回调：验签 -> 解析 -> 解密
│   ├── transfer/
│   │   ├── model.go                转账单状态机与请求/响应模型
│   │   └── service.go              发起转账 / 查单 / 撤销 + 参数校验
│   ├── store/dedupe.go             回调通知内存去重（第一道防线）
│   ├── downstream/fastapi.go       推送解密结果给 FastAPI
│   └── httpsrv/                    路由、鉴权中间件、错误处理
├── Dockerfile / Makefile / .env.example
└── go.mod                          无第三方依赖
```

---

## 4. 配置

复制 `.env.example` 为 `.env` 后按需修改。全部为环境变量，无配置文件。

关键项：

| 变量 | 说明 |
| --- | --- |
| `WXPAY_MCH_ID` / `WXPAY_APP_ID` | 商户号与服务号 appid（须已在商户平台完成绑定） |
| `WXPAY_CERT_SERIAL_NO` / `WXPAY_PRIVATE_KEY_PATH` | 商户 API 证书序列号与私钥文件 |
| `WXPAY_API_V3_KEY` | **必须 32 字符**，否则启动即失败 |
| `WXPAY_PUBLIC_KEY_ID` / `WXPAY_PUBLIC_KEY_PATH` | 微信支付公钥（推荐，长期有效） |
| `WXPAY_NOTIFY_BASE_URL` | 公网 HTTPS 基址，最终 `notify_url = {base}/wxpay/notify/transfer` |
| `WXPAY_MAX_TRANSFER_AMOUNT_FEN` | 单笔上限（分），默认 20000；提额成功后需同步调整 |
| `WXPAY_FASTAPI_NOTIFY_TIMEOUT` | 推送 FastAPI 超时，**必须 ≤ 4 秒**（回调须 5 秒内应答） |
| `WXPAY_INTERNAL_TOKEN` | 与 FastAPI 侧一致的内部调用令牌 |

启动前置（非代码）：
- 商户平台设置**接口安全 IP** = 本服务出口 IP（未设置会报「此IP地址不允许调用接口」）
- 已开通商家转账并选定转账场景（现金营销 `1000`）
- 已设置 APIv3 密钥（**未设置则微信不会发送回调**）

---

## 5. 接口契约

所有 `/internal/**` 需请求头 `X-Internal-Token`。

### 5.1 内部接口（FastAPI → Go）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查（无需鉴权） |
| POST | `/internal/transfer/create` | 发起转账 |
| GET | `/internal/transfer/query/{out_bill_no}` | 按商户单号查单 |
| POST | `/internal/transfer/cancel` | 撤销转账（仅运营后台） |

`POST /internal/transfer/create` 请求体：

```json
{
  "out_bill_no": "T20261006000001",
  "openid": "o-MYE42l80oelYMDE34nYD456Xoy",
  "user_name": "",
  "transfer_amount": 100,
  "transfer_remark": "新会员开通有礼",
  "scene_name": "新会员有礼",
  "reward_desc": "注册会员抽奖一等奖",
  "user_recv_perception": "现金奖励",
  "transfer_scene_id": "1000"
}
```

响应：

```json
{"code":"OK","data":{"out_bill_no":"...","transfer_bill_no":"...","state":"WAIT_USER_CONFIRM","package_info":"affffddafdfafddffda==","create_time":"..."}}
```

错误响应（保留微信原始错误码，便于调用方判定）：

```json
{"code":"SYSTEM_ERROR","message":"系统错误","data":{"request_id":"...","system_error":true,"detail":{...}}}
```

> 调用方约定：`system_error = true` 时**必须先用原 `out_bill_no` 查单**，确认已到终态后才可换单重试。

### 5.2 回调接口（微信 → Go）

`POST /wxpay/notify/transfer`，处理流程：
1. 读取原始 body → 2. 验签 → 3. 解密 → 4. `notify_id` 内存去重 → 5. 推送 FastAPI → 6. 应答

- 成功：`200` 空包体
- 推送 FastAPI 失败：`500`（微信会按 0s/15s×3/300s×3/1800s×3/1h×22 重试）
- 验签失败：`401`，**不得更新任何业务状态**

### 5.3 推送 FastAPI（Go → FastAPI）

`POST {WXPAY_FASTAPI_BASE_URL}/internal/wxpay/notify/transfer`，头 `X-Internal-Token`：

```json
{
  "notify_id":"EV-20261006-xxx","event_type":"MCHTRANSFER.BILL.FINISHED",
  "notify_create_time":"...","original_type":"mch-transfer",
  "mch_id":"...","appid":"...","out_bill_no":"...","transfer_bill_no":"...",
  "openid":"...","state":"SUCCESS","transfer_amount":100,
  "transfer_remark":"...","fail_reason":"","create_time":"...","update_time":"..."
}
```

> 最终幂等由 FastAPI 侧 `wxpay_notify_log.notify_id` 唯一索引保证；Go 侧内存去重只是减少重复推送。

---

## 6. 关键约束（代码已强制）

- `out_bill_no` 只能数字 + 大小写字母，≤32 字符；**每次新建必须是新值**
- `transfer_amount` 单位**分**，最小 10（0.1 元）；≥200000（2000 元）必须传 `user_name`
- 报备信息「活动名称」+「奖励说明」必须同时非空，缺一即报 `PARAM_ERROR`
- `transfer_remark` ≤32 字符
- 单笔上限由 `WXPAY_MAX_TRANSFER_AMOUNT_FEN` 前置校验（默认 200 元）
- 终态判定 `IsTerminal()`：`SUCCESS` / `FAIL` / `CANCELLED`
- `package_info` 复用判定 `CanReusePackageInfo()`：**仅 `WAIT_USER_CONFIRM`**

---

## 7. 构建与运行

```bash
# 环境：Go 1.21+（已在 Go 1.25 验证；无需 go mod download，零依赖）
make fmt vet test      # 格式化、静态检查、单元测试
make build             # 产出 bin/wxpay-service
make run               # 直接运行（需先 export 环境变量）
make docker            # 构建镜像
make test-race         # 竞态检测（需 cgo / gcc）
```

单元测试覆盖：签名/验签往返、篡改检测、AES-GCM 加解密往返与篡改检测、RSA-OAEP、状态机终态与 `package_info` 复用判定、`out_bill_no` 校验、去重器 TTL。

---

## 8. FastAPI 侧待办（配合本服务）

1. 新增 `app/modules/wxpay/`（`api.py` / `client.py` / `service.py` / `models.py` / `tasks.py`）
2. 建表：`reward_record` / `transfer_bill` / `wxpay_notify_log`（见方案第 7 章）
3. 实现 `POST /internal/wxpay/notify/transfer` 接收推送（验 token + `notify_id` 唯一索引幂等）
4. 实现提现三分支决策（方案 4.3）与 `reward_id` 并发锁
5. 兜底查单定时任务（转账单 30 天内可查）+ 奖励过期任务 + 状态一致性巡检
6. 前端「我的历史奖励」页与 `requestMerchantTransfer` 调起确认页

---

## 9. 官方文档索引

- 商家转账开发指引：https://pay.weixin.qq.com/doc/v3/merchant/4012715211
- 发起转账：https://pay.weixin.qq.com/doc/v3/merchant/4012716434
- JSAPI 调起用户确认收款：https://pay.weixin.qq.com/doc/v3/merchant/4012716430
- 商家转账回调通知：https://pay.weixin.qq.com/doc/v3/merchant/4012712115
- 总述-APIv3 如何签名和验签：https://pay.weixin.qq.com/doc/v3/merchant/4012365342
- 回调通知注意事项：https://pay.weixin.qq.com/doc/v3/merchant/4012075420
