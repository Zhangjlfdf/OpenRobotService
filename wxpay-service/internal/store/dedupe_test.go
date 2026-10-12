package store

import (
	"testing"
	"time"
)

func TestDeduper(t *testing.T) {
	d := NewDeduper(time.Hour, 100)

	if d.Seen("EV-001") {
		t.Fatal("first occurrence must not be duplicate")
	}
	if !d.Seen("EV-001") {
		t.Fatal("second occurrence must be duplicate")
	}
	if d.Seen("EV-002") {
		t.Fatal("different id must not be duplicate")
	}
}

func TestDeduperTTLExpiry(t *testing.T) {
	d := NewDeduper(10*time.Millisecond, 100)
	if d.Seen("EV-003") {
		t.Fatal("first occurrence must not be duplicate")
	}
	time.Sleep(20 * time.Millisecond)
	if d.Seen("EV-003") {
		t.Fatal("entry should expire after TTL")
	}
}

func TestDeduperEmptyID(t *testing.T) {
	d := NewDeduper(time.Hour, 100)
	if d.Seen("") || d.Seen("") {
		t.Fatal("empty id should never be treated as duplicate")
	}
}
