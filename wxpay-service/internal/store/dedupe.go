// Package store 提供回调通知的内存去重。
//
// 真正的幂等由 FastAPI 侧 wxpay_notify_log.notify_id 唯一索引保证；
// 本去重只是第一道防线，避免同一通知在极短间隔内被重复推送。
package store

import (
	"sync"
	"time"
)

// Deduper 基于 TTL 的内存去重器。
type Deduper struct {
	mu      sync.Mutex
	seen    map[string]time.Time
	ttl     time.Duration
	maxSize int
}

// NewDeduper 构造去重器。ttl 建议 >= 24h（覆盖微信回调的最长重试窗口）。
func NewDeduper(ttl time.Duration, maxSize int) *Deduper {
	if ttl <= 0 {
		ttl = 24 * time.Hour
	}
	if maxSize <= 0 {
		maxSize = 100000
	}
	return &Deduper{seen: make(map[string]time.Time), ttl: ttl, maxSize: maxSize}
}

// Seen 记录 id；返回 true 表示此前已经出现过（重复通知）。
func (d *Deduper) Seen(id string) bool {
	if id == "" {
		return false
	}
	now := time.Now()
	d.mu.Lock()
	defer d.mu.Unlock()

	if t, ok := d.seen[id]; ok && now.Sub(t) < d.ttl {
		return true
	}
	// 超过 TTL 视为新通知，重新计时。
	if len(d.seen) >= d.maxSize {
		d.evictLocked(now)
	}
	d.seen[id] = now
	return false
}

func (d *Deduper) evictLocked(now time.Time) {
	for id, t := range d.seen {
		if now.Sub(t) >= d.ttl {
			delete(d.seen, id)
		}
	}
	// 仍然满则清空一半（极端情况下的兜底，正常流量不会触发）。
	if len(d.seen) >= d.maxSize {
		n := 0
		for id := range d.seen {
			delete(d.seen, id)
			n++
			if n >= d.maxSize/2 {
				break
			}
		}
	}
}
