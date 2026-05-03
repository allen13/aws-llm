package experiments

import (
	"os"
	"os/exec"
	"testing"
)

// RunFullCorpus is the 94k-call run skeleton (PLAN §Goal). It expects the
// extract pipeline (`python -m extract_batch`) to consume a list of call IDs
// from S3 and dispatch them in batches against the warm vLLM endpoint.
func RunFullCorpus(t *testing.T, endpoint, lambdaArn, runId string) {
	t.Helper()

	// TODO: read 94k call IDs from env-configured S3 path.
	// CALL_IDS_S3_URI is the missing input source — see PLAN §Repo layout
	// (extract_batch.py) and PLAN §Reproducibility.
	callIDsURI := os.Getenv("CALL_IDS_S3_URI")
	if callIDsURI == "" {
		t.Skip("CALL_IDS_S3_URI not set; skipping full_corpus run")
	}

	// TODO: dispatch in batches via the extract pipeline (subprocess
	// invocation of `python -m extract_batch`).
	cmd := exec.Command("python", "-m", "extract_batch",
		"--endpoint", endpoint,
		"--run-id", runId,
		"--call-ids-uri", callIDsURI,
		"--query-lambda-arn", lambdaArn,
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	// TODO: monitor progress via periodic helpers.GetExperimentRun /
	// helpers.ListSuccessfulCallIds polls so a 24h run can be observed
	// without tailing logs.
	t.Logf("full_corpus skeleton: would run %v for run_id=%s", cmd.Args, runId)
}
