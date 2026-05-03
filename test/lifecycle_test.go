package main

import (
	"os"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	test_structure "github.com/gruntwork-io/terratest/modules/test-structure"
	"github.com/stretchr/testify/require"

	"github.com/your-org/aws-llm/test/experiments"
	"github.com/your-org/aws-llm/test/helpers"
)

// TestLifecycle is the 3-stage create/run/destroy harness for infra/runtime/.
// Stages skip independently via SKIP_create / SKIP_run / SKIP_destroy.
func TestLifecycle(t *testing.T) {
	workDir := "./.test-data"

	test_structure.RunTestStage(t, "create", func() {
		opts := helpers.TerraformOptionsRuntime(t)
		terraform.InitAndApply(t, opts)
		endpoint := terraform.Output(t, opts, "vllm_endpoint")
		require.NoError(t, helpers.WaitForReady(t, endpoint, 20*time.Minute))
		require.NoError(t, helpers.AssertFlashInferFP8Active(t, endpoint))
		runId := helpers.RunIDFor(t.Name())
		// TODO: insert experiment_runs row with status='running' via the
		// query Lambda once a writer path exists (Athena workgroup is
		// read-only; needs a separate writer Lambda or PyIceberg sidecar).
		test_structure.SaveTerraformOptions(t, workDir, opts)
		test_structure.SaveString(t, workDir, "endpoint", endpoint)
		test_structure.SaveString(t, workDir, "runId", runId)
	})

	test_structure.RunTestStage(t, "run", func() {
		opts := test_structure.LoadTerraformOptions(t, workDir)
		endpoint := test_structure.LoadString(t, workDir, "endpoint")
		runId := test_structure.LoadString(t, workDir, "runId")
		lambdaArn := os.Getenv("QUERY_LAMBDA_ARN")
		switch os.Getenv("EXPERIMENT") {
		case "prefix_cache":
			experiments.RunPrefixCache(t, endpoint, lambdaArn, runId)
		case "concurrency_bench":
			experiments.RunConcurrencyBench(t, endpoint, lambdaArn, runId)
		case "reasoning_quality":
			experiments.RunReasoningQuality(t, endpoint, lambdaArn, runId)
		case "constrained_decoding":
			experiments.RunConstrainedDecoding(t, endpoint, runId)
		case "spot_interrupt":
			experiments.RunSpotInterrupt(t, endpoint, lambdaArn, runId)
		case "truncation_ab":
			experiments.RunTruncationAB(t, endpoint, lambdaArn, runId)
		case "full_corpus":
			experiments.RunFullCorpus(t, endpoint, lambdaArn, runId)
		default:
			t.Fatalf("unknown EXPERIMENT=%q", os.Getenv("EXPERIMENT"))
		}
		_ = opts // suppress unused if no destroy here
	})

	test_structure.RunTestStage(t, "destroy", func() {
		opts := test_structure.LoadTerraformOptions(t, workDir)
		// TODO: update experiment_runs row with ended_at + final status via
		// the writer path (see TODO in create stage).
		helpers.RuntimeStackDown(t, opts)
	})
}
