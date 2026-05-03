package experiments

import (
	"context"
	"os"
	"testing"

	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/fis"
)

// fisClient is the (currently unused) hook for triggering the
// "spot-interrupt" FIS template. Held here so aws-sdk-go-v2/service/fis
// stays in go.mod and is ready to wire up per PLAN §Spot handling §Test.
//
//nolint:unused
var _ = func() *fis.Client {
	cfg, err := awsconfig.LoadDefaultConfig(context.Background())
	if err != nil {
		return nil
	}
	return fis.NewFromConfig(cfg)
}

// RunSpotInterrupt is a skeleton for the spot-interruption end-to-end test
// described in PLAN §Spot handling §Test. It requires the AWS FIS template
// "spot-interrupt" and is skipped unless USE_SPOT=true.
//
// TODO: requires AWS FIS template "spot-interrupt"; see PLAN §Spot handling §Test.
func RunSpotInterrupt(t *testing.T, endpoint, lambdaArn, runId string) {
	t.Helper()
	if os.Getenv("USE_SPOT") != "true" {
		t.Skip("USE_SPOT != true; skipping spot interruption test")
	}

	// 1. Start a 100-call experiment; wait until ~10 calls have committed.
	// TODO: dispatch the extract pipeline as a subprocess against `endpoint`
	// with a known input list and run_id.

	// 2. Trigger an interruption via AWS FIS template "spot-interrupt".
	// TODO: use aws-sdk-go-v2/service/fis StartExperiment with the configured
	// experiment template ID (env: FIS_SPOT_TEMPLATE_ID).

	// 3. Assert: watcher logs signal, extract process flushes within 90 s,
	// instance terminates within 120 s.
	// TODO: poll CloudWatch Logs Insights for the spot-watcher log line;
	// poll experiment_runs[run_id].status == 'interrupted' via lambdaArn.

	// 4. Assert: experiment_runs[run_id].status = 'interrupted'.
	// 5. Assert: no torn batches (every committed call_id is complete).

	// 6. Re-run via resume.sh-equivalent: set RESUME_RUN_ID and dispatch.
	resumeID := os.Getenv("RESUME_RUN_ID")
	if resumeID == "" {
		resumeID = runId
	}
	// TODO: assert skip set excludes already-processed calls and final
	// status = 'success' with row count == 100 via lambdaArn.

	_ = endpoint
	_ = lambdaArn
	_ = resumeID
	t.Logf("spot_interrupt skeleton for run_id=%s — implementation pending FIS template wiring", runId)
}
