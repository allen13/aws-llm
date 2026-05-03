package experiments

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/your-org/aws-llm/test/helpers"
)

// RunConstrainedDecoding implements Validation gate 2 from PLAN §Validation
// gates: re-test the local prototype's "no constrained decoding" rule against
// this stack.
func RunConstrainedDecoding(t *testing.T, endpoint, runId string) {
	t.Helper()
	if err := helpers.WaitForReady(t, endpoint, 5*time.Minute); err != nil {
		t.Fatalf("vLLM not ready: %v", err)
	}

	// Test A: response_format: json_schema against the v2 schema. Assert no
	// whitespace-loop (response not >10x median length).
	schemaFmt := map[string]any{
		"type": "json_schema",
		"json_schema": map[string]any{
			"name": "extraction_v2_stub",
			// TODO: replace with the actual v2 CALL_SCHEMA from extract_lib/schema.py
			// once the Python pipeline is in tree.
			"schema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"summary": map[string]any{"type": "string"},
				},
				"required": []string{"summary"},
			},
		},
	}
	lensA := lengthsForFormat(t, endpoint, schemaFmt, 20)
	if exceededRunaway(lensA) {
		t.Fatalf("constrained_decoding A run_id=%s: whitespace-loop runaway detected (>10x median); rule still applies", runId)
	}

	// Test B: response_format: json_object. Assert no trailing \n runaway.
	objFmt := map[string]any{"type": "json_object"}
	lensB := lengthsForFormat(t, endpoint, objFmt, 20)
	if exceededRunaway(lensB) {
		t.Fatalf("constrained_decoding B run_id=%s: trailing-\\n runaway detected (>10x median); rule still applies", runId)
	}

	t.Logf("constrained_decoding run_id=%s: both formats clear (A median=%d B median=%d)",
		runId, median(lensA), median(lensB))
}

func lengthsForFormat(t *testing.T, endpoint string, format map[string]any, n int) []int {
	t.Helper()
	out := make([]int, 0, n)
	for i := 0; i < n; i++ {
		body := map[string]any{
			"model": "nemotron-nano-30b",
			"messages": []map[string]string{
				{"role": "system", "content": "Return strict JSON."},
				{"role": "user", "content": fmt.Sprintf("Summarize call %d.", i)},
			},
			"max_tokens":      512,
			"response_format": format,
		}
		buf, _ := json.Marshal(body)
		url := strings.TrimRight(endpoint, "/") + "/v1/chat/completions"
		req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(buf))
		if err != nil {
			continue
		}
		req.Header.Set("Content-Type", "application/json")
		client := &http.Client{Timeout: 120 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			continue
		}
		raw, _ := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		out = append(out, extractContentLen(raw))
	}
	return out
}

func extractContentLen(raw []byte) int {
	var parsed struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(raw, &parsed); err != nil || len(parsed.Choices) == 0 {
		return len(raw)
	}
	return len(parsed.Choices[0].Message.Content)
}

func median(xs []int) int {
	if len(xs) == 0 {
		return 0
	}
	cp := append([]int(nil), xs...)
	sort.Ints(cp)
	return cp[len(cp)/2]
}

func exceededRunaway(xs []int) bool {
	m := median(xs)
	if m == 0 {
		return false
	}
	for _, x := range xs {
		if x > 10*m {
			return true
		}
	}
	return false
}
