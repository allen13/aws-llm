package experiments

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/your-org/aws-llm/test/helpers"
)

// RunPrefixCache implements PLAN §Validation gates #3 (prefix cache > 0.7).
// It sends 50 warm-up calls and 50 measurement calls through the
// OpenAI-compatible endpoint, then scrapes /metrics for the cache hit ratio.
func RunPrefixCache(t *testing.T, endpoint, lambdaArn, runId string) {
	t.Helper()
	if err := helpers.WaitForReady(t, endpoint, 5*time.Minute); err != nil {
		t.Fatalf("vLLM not ready: %v", err)
	}

	const warmup = 50
	const measure = 50
	for i := 0; i < warmup; i++ {
		_ = postCompletion(endpoint, fmt.Sprintf("warmup transcript %d", i))
	}
	for i := 0; i < measure; i++ {
		_ = postCompletion(endpoint, fmt.Sprintf("measurement transcript %d", i))
	}

	ratio, err := scrapePrefixCacheHitRatio(endpoint)
	if err != nil {
		t.Fatalf("scrape /metrics: %v", err)
	}
	t.Logf("prefix cache hit ratio for run_id=%s: %.4f", runId, ratio)
	if ratio <= 0.7 {
		t.Fatalf("prefix cache hit ratio %.4f <= 0.7 (validation gate 3 failed)", ratio)
	}
	_ = lambdaArn
}

// postCompletion sends a small chat-completions call to the OpenAI-compatible
// endpoint. Errors are intentionally swallowed; callers care about cache state.
func postCompletion(endpoint, userText string) error {
	body := map[string]any{
		"model": "nemotron-nano-30b",
		"messages": []map[string]string{
			{"role": "system", "content": "You are an extraction assistant."},
			{"role": "user", "content": userText},
		},
		"max_tokens": 16,
	}
	buf, _ := json.Marshal(body)
	url := strings.TrimRight(endpoint, "/") + "/v1/chat/completions"
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(buf))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	return resp.Body.Close()
}

// scrapePrefixCacheHitRatio parses Prometheus-style /metrics for vLLM's
// prefix cache hit ratio. Falls back to a hits/(hits+misses) computation
// when only counters are exposed.
func scrapePrefixCacheHitRatio(endpoint string) (float64, error) {
	url := strings.TrimRight(endpoint, "/") + "/metrics"
	resp, err := http.Get(url)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	text := string(body)

	// Direct gauge form (newer vLLM exposes this).
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "vllm:gpu_prefix_cache_hit_rate") ||
			strings.HasPrefix(line, "vllm:prefix_cache_hit_rate") {
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				if v, err := strconv.ParseFloat(parts[len(parts)-1], 64); err == nil {
					return v, nil
				}
			}
		}
	}

	// Fall back to counters.
	hitRe := regexp.MustCompile(`(?m)^vllm:[a-z_]*prefix[a-z_]*hit[a-z_]*\s+([0-9.eE+-]+)`)
	queryRe := regexp.MustCompile(`(?m)^vllm:[a-z_]*prefix[a-z_]*quer(?:y|ies)[a-z_]*\s+([0-9.eE+-]+)`)
	hits := sumMatches(hitRe, text)
	queries := sumMatches(queryRe, text)
	if queries > 0 {
		return hits / queries, nil
	}
	return 0, fmt.Errorf("prefix cache metric not found in /metrics")
}

func sumMatches(re *regexp.Regexp, text string) float64 {
	var total float64
	for _, m := range re.FindAllStringSubmatch(text, -1) {
		if len(m) < 2 {
			continue
		}
		if v, err := strconv.ParseFloat(m[1], 64); err == nil {
			total += v
		}
	}
	return total
}
