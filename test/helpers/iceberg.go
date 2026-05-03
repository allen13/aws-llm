package helpers

import (
	"fmt"
	"testing"
	"time"
)

// ExperimentRun mirrors the experiment_runs row schema in PLAN §Data layer.
type ExperimentRun struct {
	RunID             string    `json:"run_id"`
	TestName          string    `json:"test_name"`
	GitSha            string    `json:"git_sha"`
	ModelManifestHash string    `json:"model_manifest_hash"`
	VLLMImageURI      string    `json:"vllm_image_uri"`
	InstanceType      string    `json:"instance_type"`
	UseSpot           bool      `json:"use_spot"`
	Region            string    `json:"region"`
	EnableThinking    bool      `json:"enable_thinking"`
	MaxModelLen       int       `json:"max_model_len"`
	MaxNumSeqs        int       `json:"max_num_seqs"`
	StartedAt         time.Time `json:"started_at"`
	EndedAt           time.Time `json:"ended_at"`
	Status            string    `json:"status"`
	Notes             string    `json:"notes"`
}

// BenchMeasurement mirrors the bench_measurements row schema in PLAN §Data layer.
type BenchMeasurement struct {
	RunID               string    `json:"run_id"`
	Concurrency         int       `json:"concurrency"`
	ReqPerSec           float64   `json:"req_per_sec"`
	ErrorRate           float64   `json:"error_rate"`
	PrefixCacheHitRatio float64   `json:"prefix_cache_hit_ratio"`
	GPUUtilMean         float64   `json:"gpu_util_mean"`
	TotalCalls          int       `json:"total_calls"`
	DurationSeconds     float64   `json:"duration_seconds"`
	MeasuredAt          time.Time `json:"measured_at"`
}

// GetExperimentRun calls the summarize_run template via the query Lambda.
func GetExperimentRun(t *testing.T, lambdaArn, runId string) (*ExperimentRun, error) {
	t.Helper()
	resp, err := InvokeQueryLambda(t, lambdaArn, QueryRequest{
		Template: "summarize_run",
		Params:   map[string]any{"run_id": runId},
	})
	if err != nil {
		return nil, err
	}
	if len(resp.Rows) == 0 {
		return nil, fmt.Errorf("no experiment_runs row found for run_id=%s", runId)
	}
	// TODO: map columns -> ExperimentRun fields by name. The template's
	// projection should be aligned with the struct above; for now return a
	// minimal scaffold so callers compile.
	return &ExperimentRun{RunID: runId}, nil
}

// ListSuccessfulCallIds runs a free-form SQL via the query Lambda and returns
// the list of call_ids whose extraction_status = 'success' for the run.
func ListSuccessfulCallIds(t *testing.T, lambdaArn, runId string) ([]string, error) {
	t.Helper()
	sql := fmt.Sprintf(
		"SELECT call_id FROM nemo.calls_extractions WHERE run_id = '%s' AND extraction_status = 'success'",
		runId,
	)
	resp, err := InvokeQueryLambda(t, lambdaArn, QueryRequest{SQL: sql})
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(resp.Rows))
	for _, row := range resp.Rows {
		if len(row) == 0 {
			continue
		}
		if s, ok := row[0].(string); ok {
			out = append(out, s)
		}
	}
	return out, nil
}

// GetBenchMeasurements calls the compare_concurrency template for a given test.
func GetBenchMeasurements(t *testing.T, lambdaArn, testName string) ([]BenchMeasurement, error) {
	t.Helper()
	resp, err := InvokeQueryLambda(t, lambdaArn, QueryRequest{
		Template: "compare_concurrency",
		Params:   map[string]any{"test_name": testName},
	})
	if err != nil {
		return nil, err
	}
	// TODO: map columns -> BenchMeasurement. Stub returns empty slice with
	// row count for now so build passes.
	out := make([]BenchMeasurement, 0, len(resp.Rows))
	return out, nil
}
