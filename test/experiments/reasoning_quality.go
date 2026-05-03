package experiments

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/your-org/aws-llm/test/helpers"
)

// RunReasoningQuality runs a 100-call A/B with enable_thinking={false,true},
// per PLAN §Model. Captures per-call latency; quality metric TODO.
func RunReasoningQuality(t *testing.T, endpoint, lambdaArn, runId string) {
	t.Helper()
	if err := helpers.WaitForReady(t, endpoint, 5*time.Minute); err != nil {
		t.Fatalf("vLLM not ready: %v", err)
	}

	const callsPerArm = 100
	for _, thinking := range []bool{false, true} {
		var totalLatency time.Duration
		var ok int
		for i := 0; i < callsPerArm; i++ {
			start := time.Now()
			if err := postReasoningCompletion(endpoint, thinking, fmt.Sprintf("quality call %d", i)); err == nil {
				ok++
				totalLatency += time.Since(start)
			}
		}
		mean := time.Duration(0)
		if ok > 0 {
			mean = totalLatency / time.Duration(ok)
		}
		// TODO: define an actual quality proxy metric (schema-validity rate,
		// extracted-field agreement vs. BF16 reference, etc.) per PLAN §Model
		// optional experiment.
		t.Logf("reasoning_quality run_id=%s enable_thinking=%v ok=%d mean_latency=%s",
			runId, thinking, ok, mean)
	}
	_ = lambdaArn
}

func postReasoningCompletion(endpoint string, enableThinking bool, userText string) error {
	body := map[string]any{
		"model": "nemotron-nano-30b",
		"messages": []map[string]string{
			{"role": "system", "content": "You are an extraction assistant."},
			{"role": "user", "content": userText},
		},
		"max_tokens": 64,
		"chat_template_kwargs": map[string]any{
			"enable_thinking": enableThinking,
		},
	}
	buf, _ := json.Marshal(body)
	url := strings.TrimRight(endpoint, "/") + "/v1/chat/completions"
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(buf))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}
