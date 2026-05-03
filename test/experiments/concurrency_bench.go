package experiments

import (
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/your-org/aws-llm/test/helpers"
)

// RunConcurrencyBench sweeps [16, 24, 32, 40, 48] (per PLAN §Concurrency) and
// records req/s, error rate, prefix-cache hit ratio, and GPU util at each
// level.
func RunConcurrencyBench(t *testing.T, endpoint, lambdaArn, runId string) {
	t.Helper()
	if err := helpers.WaitForReady(t, endpoint, 5*time.Minute); err != nil {
		t.Fatalf("vLLM not ready: %v", err)
	}

	levels := []int{16, 24, 32, 40, 48}
	const totalCalls = 200

	for _, c := range levels {
		start := time.Now()
		var ok, fail int64
		var wg sync.WaitGroup
		sem := make(chan struct{}, c)
		for i := 0; i < totalCalls; i++ {
			wg.Add(1)
			sem <- struct{}{}
			go func(idx int) {
				defer wg.Done()
				defer func() { <-sem }()
				if err := postCompletion(endpoint, fmt.Sprintf("bench call %d", idx)); err != nil {
					atomic.AddInt64(&fail, 1)
					return
				}
				atomic.AddInt64(&ok, 1)
			}(i)
		}
		wg.Wait()
		elapsed := time.Since(start).Seconds()

		rps := float64(ok) / elapsed
		errRate := float64(fail) / float64(totalCalls)
		hitRatio, _ := scrapePrefixCacheHitRatio(endpoint)
		gpuUtil := scrapeGPUUtil(endpoint) // best-effort

		// TODO: free-form INSERT through Athena is not supported. Once a
		// dedicated writer Lambda or PyIceberg-backed endpoint exists, push
		// this row to bench_measurements via helpers.InvokeQueryLambda.
		t.Logf(
			"bench run_id=%s concurrency=%d req_per_sec=%.3f error_rate=%.3f prefix_cache_hit_ratio=%.3f gpu_util_mean=%.3f total_calls=%d duration_seconds=%.2f",
			runId, c, rps, errRate, hitRatio, gpuUtil, totalCalls, elapsed,
		)
	}
	_ = lambdaArn
}

// scrapeGPUUtil pulls a coarse GPU utilization gauge from /metrics if exposed.
// Returns 0 when not present; caller treats it as best-effort.
func scrapeGPUUtil(endpoint string) float64 {
	// TODO: vLLM does not expose GPU util natively; for production this
	// should query CloudWatch (DCGM exporter or nvidia_smi sidecar). Stub.
	_ = endpoint
	return 0
}
