package httpsrv

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"

	"github.com/OpenRobotService/wxpay-service/internal/transfer"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

// transferHandler 暴露商家转账的内部接口。
type transferHandler struct {
	svc *transfer.Service
}

type createTransferRequest struct {
	OutBillNo  string `json:"out_bill_no"`
	Openid     string `json:"openid"`
	UserName   string `json:"user_name"`
	Amount     int64  `json:"transfer_amount"`
	Remark     string `json:"transfer_remark"`
	SceneName  string `json:"scene_name"`
	RewardDesc string `json:"reward_desc"`
	Perception string `json:"user_recv_perception"`
	SceneID    string `json:"transfer_scene_id"`
	NotifyURL  string `json:"notify_url"`
}

type cancelTransferRequest struct {
	OutBillNo string `json:"out_bill_no"`
}

func (h *transferHandler) create(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeFail(w, http.StatusBadRequest, "read body failed")
		return
	}
	var req createTransferRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		writeFail(w, http.StatusBadRequest, "invalid json body")
		return
	}

	res, err := h.svc.Create(r.Context(), transfer.CreateInput{
		OutBillNo:  req.OutBillNo,
		Openid:     req.Openid,
		UserName:   req.UserName,
		Amount:     req.Amount,
		Remark:     req.Remark,
		SceneName:  req.SceneName,
		RewardDesc: req.RewardDesc,
		Perception: req.Perception,
		SceneID:    req.SceneID,
		NotifyURL:  req.NotifyURL,
	})
	if err != nil {
		writeTransferError(w, err)
		return
	}
	writeOK(w, res)
}

// queryByOutBillNo 按商户单号查单。路径：GET /internal/transfer/query/{out_bill_no}
func (h *transferHandler) queryByOutBillNo(w http.ResponseWriter, r *http.Request) {
	outBillNo := strings.Trim(strings.TrimPrefix(r.URL.Path, "/internal/transfer/query/"), "/")
	if outBillNo == "" {
		writeFail(w, http.StatusBadRequest, "out_bill_no is required")
		return
	}
	// 防御路径穿越：商户单号只允许数字字母。
	if strings.Contains(outBillNo, "/") {
		writeFail(w, http.StatusBadRequest, "invalid out_bill_no")
		return
	}
	bill, err := h.svc.QueryByOutBillNo(r.Context(), outBillNo)
	if err != nil {
		writeTransferError(w, err)
		return
	}
	writeOK(w, bill)
}

func (h *transferHandler) cancel(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeFail(w, http.StatusBadRequest, "read body failed")
		return
	}
	var req cancelTransferRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		writeFail(w, http.StatusBadRequest, "invalid json body")
		return
	}
	res, err := h.svc.Cancel(r.Context(), req.OutBillNo)
	if err != nil {
		writeTransferError(w, err)
		return
	}
	writeOK(w, res)
}

// writeTransferError 把错误映射为合适的 HTTP 状态码。
//
// 关键：微信侧系统错误（SYSTEM_ERROR / 5xx）必须让调用方感知，
// 调用方需先用原 out_bill_no 查单，禁止直接换单重试。
func writeTransferError(w http.ResponseWriter, err error) {
	var apiErr *wxpay.APIError
	if errors.As(err, &apiErr) {
		status := http.StatusBadGateway
		if apiErr.StatusCode >= 400 && apiErr.StatusCode < 500 {
			status = apiErr.StatusCode
		}
		slog.Warn("wechat pay api error",
			"status", apiErr.StatusCode,
			"code", apiErr.Code,
			"message", apiErr.Message,
			"request_id", apiErr.RequestID,
		)
		writeJSON(w, status, apiErr.Code, apiErr.Message, map[string]interface{}{
			"request_id":   apiErr.RequestID,
			"system_error": apiErr.IsSystemError(),
			"detail":       apiErr.Detail,
		})
		return
	}

	slog.Warn("transfer request rejected", "error", err)
	writeFail(w, http.StatusBadRequest, err.Error())
}
