// Package config 负责从环境变量加载 wxpay-service 的全部配置。
//
// 商户私钥、APIv3 密钥等敏感信息只在本服务进程内使用，不对外暴露、不写入日志。
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// Config 是 wxpay-service 的运行时配置。
type Config struct {
	// HTTP 服务
	ListenAddr         string
	ShutdownTimeoutSec int
	ReadTimeoutSec     int
	WriteTimeoutSec    int

	// 内部调用鉴权（FastAPI -> Go）
	InternalToken string

	// 微信支付商户参数
	MchID          string
	AppID          string
	CertSerialNo   string // 商户 API 证书序列号，用于请求签名
	PrivateKeyPath string // 商户 API 证书私钥文件路径（apiclient_key.pem）
	APIv3Key       string // APIv3 密钥，32 字节，用于回调解密

	// 微信支付公钥（官方推荐，长期有效，替代 5 年一换的平台证书）
	WechatPayPublicKeyID   string // 形如 PUB_KEY_ID_xxxxxxxxxxxxx
	WechatPayPublicKeyPath string // wxp_pub.pem

	// 商家转账
	NotifyBaseURL     string // 公网 HTTPS 站点根地址，如 https://<domain>（不含路径）
	TransferSceneID   string // 转账场景 ID，现金营销默认 1000
	DefaultPerception string // 默认收款感知，仅支持「现金奖励」/「活动奖励」
	APITimeoutSec     int    // 调用微信支付接口超时

	// MaxTransferAmountFen 单笔转账上限（分），对应商户平台设置的单笔限额。
	// 默认 20000（200 元）；提额成功后需同步调整此值。0 表示不校验。
	MaxTransferAmountFen int64

	// 下游 FastAPI
	FastAPIBaseURL       string
	FastAPIToken         string
	FastAPINotifyTimeout int // 推送回调结果超时（秒），需 < 5 秒应答窗口

	LogLevel string
}

const (
	// NotifyPathTransfer 是商家转账回调在本服务上的路径（对外，由 Nginx 转发）。
	NotifyPathTransfer = "/wxpay/notify/transfer"
	// DownstreamNotifyPath 是 FastAPI 侧接收解密结果的内部接口。
	DownstreamNotifyPath = "/internal/wxpay/notify/transfer"
)

// Load 从环境变量加载并校验配置。
func Load() (*Config, error) {
	c := &Config{
		ListenAddr:         envStr("WXPAY_LISTEN_ADDR", ":9100"),
		ShutdownTimeoutSec: envInt("WXPAY_SHUTDOWN_TIMEOUT", 10),
		ReadTimeoutSec:     envInt("WXPAY_READ_TIMEOUT", 15),
		WriteTimeoutSec:    envInt("WXPAY_WRITE_TIMEOUT", 15),

		InternalToken: os.Getenv("WXPAY_INTERNAL_TOKEN"),

		MchID:          os.Getenv("WXPAY_MCH_ID"),
		AppID:          os.Getenv("WXPAY_APP_ID"),
		CertSerialNo:   os.Getenv("WXPAY_CERT_SERIAL_NO"),
		PrivateKeyPath: os.Getenv("WXPAY_PRIVATE_KEY_PATH"),
		APIv3Key:       os.Getenv("WXPAY_API_V3_KEY"),

		WechatPayPublicKeyID:   os.Getenv("WXPAY_PUBLIC_KEY_ID"),
		WechatPayPublicKeyPath: os.Getenv("WXPAY_PUBLIC_KEY_PATH"),

		NotifyBaseURL:     os.Getenv("WXPAY_NOTIFY_BASE_URL"),
		TransferSceneID:   envStr("WXPAY_TRANSFER_SCENE_ID", "1000"),
		DefaultPerception: envStr("WXPAY_USER_RECV_PERCEPTION", "现金奖励"),
		APITimeoutSec:     envInt("WXPAY_API_TIMEOUT", 10),

		MaxTransferAmountFen: int64(envInt("WXPAY_MAX_TRANSFER_AMOUNT_FEN", 20000)),

		FastAPIBaseURL:       os.Getenv("WXPAY_FASTAPI_BASE_URL"),
		FastAPIToken:         os.Getenv("WXPAY_FASTAPI_TOKEN"),
		FastAPINotifyTimeout: envInt("WXPAY_FASTAPI_NOTIFY_TIMEOUT", 3),

		LogLevel: envStr("WXPAY_LOG_LEVEL", "INFO"),
	}

	var missing []string
	require := func(name, v string) {
		if strings.TrimSpace(v) == "" {
			missing = append(missing, name)
		}
	}
	require("WXPAY_INTERNAL_TOKEN", c.InternalToken)
	require("WXPAY_MCH_ID", c.MchID)
	require("WXPAY_APP_ID", c.AppID)
	require("WXPAY_CERT_SERIAL_NO", c.CertSerialNo)
	require("WXPAY_PRIVATE_KEY_PATH", c.PrivateKeyPath)
	require("WXPAY_API_V3_KEY", c.APIv3Key)
	require("WXPAY_PUBLIC_KEY_ID", c.WechatPayPublicKeyID)
	require("WXPAY_PUBLIC_KEY_PATH", c.WechatPayPublicKeyPath)
	require("WXPAY_NOTIFY_BASE_URL", c.NotifyBaseURL)
	require("WXPAY_FASTAPI_BASE_URL", c.FastAPIBaseURL)

	if len(missing) > 0 {
		return nil, fmt.Errorf("missing required env: %s", strings.Join(missing, ", "))
	}

	// APIv3 密钥是 AES-256-GCM 的密钥，长度必须严格 32 字节。
	if len(c.APIv3Key) != 32 {
		return nil, fmt.Errorf("WXPAY_API_V3_KEY must be exactly 32 chars, got %d", len(c.APIv3Key))
	}
	if c.FastAPINotifyTimeout <= 0 || c.FastAPINotifyTimeout > 4 {
		return nil, fmt.Errorf("WXPAY_FASTAPI_NOTIFY_TIMEOUT must be in (0,4], got %d", c.FastAPINotifyTimeout)
	}
	if c.DefaultPerception != "现金奖励" && c.DefaultPerception != "活动奖励" {
		return nil, fmt.Errorf("WXPAY_USER_RECV_PERCEPTION must be 现金奖励 or 活动奖励, got %q", c.DefaultPerception)
	}

	return c, nil
}

// NotifyURL 返回商家转账回调的完整公网地址。
func (c *Config) NotifyURL() string {
	return strings.TrimRight(c.NotifyBaseURL, "/") + NotifyPathTransfer
}

func envStr(key, def string) string {
	if v := os.Getenv(key); strings.TrimSpace(v) != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	raw := os.Getenv(key)
	if strings.TrimSpace(raw) == "" {
		return def
	}
	v, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil {
		return def
	}
	return v
}
