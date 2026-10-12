package httpsrv

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"

	"github.com/OpenRobotService/wxpay-service/internal/config"
	"github.com/OpenRobotService/wxpay-service/internal/downstream"
	"github.com/OpenRobotService/wxpay-service/internal/store"
	"github.com/OpenRobotService/wxpay-service/internal/transfer"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

// EventTypeTransferFinished 是商家转账的回调事件类型。
const EventTypeTransferFinished = "MCHTRANSFER.BILL.FINISHED"

// notifyHandler 处理微信支付回调（对外，无内部鉴权）。
type notifyHandler struct {
	cfg        *config.Config
	keys       wxpay.KeyProvider
	deduper    *store.Deduper
	downstream *downstream.Client
}

func (h *notifyHandler) transfer(w http.ResponseWriter, r *http.Request) {
	// 1. 读取原始报文体（禁止任何重序列化）。
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		wechatFail(w, http.StatusBadRequest, "read body failed")
		return
	}

	// 2. 验签 -> 解析 -> 解密（顺序不可变）。
	notification, plain, err := wxpay.VerifyAndParse(h.keys, h.cfg.APIv3Key, r.Header, raw)
	if err != nil {
		// 验签失败必须拒绝，且不得更新任何业务状态。
		slog.Warn("notification verification failed", "error", err, "path", r.URL.Path)
		wechatFail(w, http.StatusUnauthorized, "signature verification failed")
		return
	}

	if notification.EventType != EventTypeTransferFinished {
		slog.Warn("unexpected event type", "event_type", notification.EventType)
		wechatFail(w, http.StatusBadRequest, "unexpected event type")
		return
	}

	// 3. 解密后的业务内容。
	var res transfer.NotifyResource
	if err := json.Unmarshal(plain, &res); err != nil {
		slog.Error("unmarshal decrypted resource failed", "error", err)
		wechatFail(w, http.StatusBadRequest, "invalid resource")
		return
	}

	// 4. 幂等：同一通知可能多次送达（第一道防线；最终幂等由 FastAPI 唯一索引保证）。
	if h.deduper.Seen(notification.ID) {
		slog.Info("duplicate notification skipped", "notify_id", notification.ID)
		wechatOK(w)
		return
	}

	// 5. 推送给 FastAPI；失败则应答 5xx 让微信重试。
	err = h.downstream.PushTransferNotify(r.Context(), downstream.TransferNotifyPayload{
		NotifyID:         notification.ID,
		EventType:        notification.EventType,
		NotifyCreateTime: notification.CreateTime,
		OriginalType:     notification.Resource.OriginalType,
		MchID:            res.MchID,
		AppID:            res.Appid,
		OutBillNo:        res.OutBillNo,
		TransferBillNo:   res.TransferBillNo,
		Openid:           res.Openid,
		State:            string(res.State),
		TransferAmount:   res.TransferAmount,
		TransferRemark:   res.TransferRemark,
		FailReason:       res.FailReason,
		CreateTime:       res.CreateTime,
		UpdateTime:       res.UpdateTime,
	})
	if err != nil {
		slog.Error("push notify to fastapi failed",
			"notify_id", notification.ID,
			"out_bill_no", res.OutBillNo,
			"state", res.State,
			"error", err,
		)
		wechatFail(w, http.StatusInternalServerError, "downstream unavailable")
		return
	}

	slog.Info("transfer notification processed",
		"notify_id", notification.ID,
		"out_bill_no", res.OutBillNo,
		"transfer_bill_no", res.TransferBillNo,
		"state", res.State,
		"amount", res.TransferAmount,
	)
	wechatOK(w)
}
