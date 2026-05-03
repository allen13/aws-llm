package helpers

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"testing"
	"time"

	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatch"
)

// cloudWatchClient is the (currently unused) hook for the CloudWatch Logs
// fallback in AssertFlashInferFP8Active. Held here so the dependency is
// retained by `go mod tidy` and ready to wire up.
//
//nolint:unused
var _ = func() *cloudwatch.Client {
	cfg, err := awsconfig.LoadDefaultConfig(context.Background())
	if err != nil {
		return nil
	}
	return cloudwatch.NewFromConfig(cfg)
}

// WaitForReady polls ${endpoint}/v1/models until 200 or timeout.
func WaitForReady(t *testing.T, endpoint string, timeout time.Duration) error {
	t.Helper()
	url := strings.TrimRight(endpoint, "/") + "/v1/models"
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 10 * time.Second}
	var lastErr error
	for time.Now().Before(deadline) {
		req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, url, nil)
		if err != nil {
			return err
		}
		resp, err := client.Do(req)
		if err == nil {
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
			lastErr = fmt.Errorf("status %d", resp.StatusCode)
		} else {
			lastErr = err
		}
		time.Sleep(5 * time.Second)
	}
	return fmt.Errorf("vLLM not ready at %s within %s: %w", url, timeout, lastErr)
}

// AssertFlashInferFP8Active is Validation gate 1 from PLAN §Validation gates.
// GETs ${endpoint}/metrics, also reads CloudWatch logs for the instance to grep
// for "FlashInfer" + "FP8". Returns error if neither source confirms.
func AssertFlashInferFP8Active(t *testing.T, endpoint string) error {
	t.Helper()
	metricsURL := strings.TrimRight(endpoint, "/") + "/metrics"
	client := &http.Client{Timeout: 15 * time.Second}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, metricsURL, nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err == nil {
		body, _ := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		text := string(body)
		if strings.Contains(text, "FlashInfer") && strings.Contains(text, "FP8") {
			return nil
		}
		if strings.Contains(strings.ToLower(text), "flashinfer") && strings.Contains(strings.ToLower(text), "fp8") {
			return nil
		}
	}
	// TODO: fall back to CloudWatch Logs Insights query for the instance log
	// group, grepping for "FlashInfer" and "FP8" in the vLLM startup output.
	// For now, surface a clear error.
	return fmt.Errorf("FlashInfer FP8 kernel path not confirmed via /metrics at %s (CloudWatch fallback not yet implemented)", metricsURL)
}

// RunIDFor builds the run_id per PLAN §Data layer:
// run_id = git_sha + "_" + started_at_unix_ms.
func RunIDFor(testName string) string {
	_ = testName
	return fmt.Sprintf("%s_%d", gitSha(), time.Now().UnixMilli())
}

// gitSha returns the short git SHA of HEAD, or "unknown" on error.
func gitSha() string {
	out, err := exec.Command("git", "rev-parse", "--short", "HEAD").Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(out))
}
