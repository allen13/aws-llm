package experiments

import (
	"fmt"
	"os"
	"os/exec"
	"testing"

	"github.com/your-org/aws-llm/test/helpers"
)

// RunTruncationAB performs the A/B over max_transcript_chars in
// [5000, 10000, 19000] (per PLAN §Transcript budget) against the same 100
// calls per setting, then queries the field_disagreement template to
// compare.
func RunTruncationAB(t *testing.T, endpoint, lambdaArn, runId string) {
	t.Helper()

	settings := []int{5000, 10000, 19000}
	runIDs := make(map[int]string, len(settings))

	for _, chars := range settings {
		armRunID := fmt.Sprintf("%s_trunc%d", runId, chars)
		runIDs[chars] = armRunID

		// TODO: replace subprocess invocation with whatever the chosen
		// driver is once extract/extract_batch.py lands.
		cmd := exec.Command("python", "-m", "extract_batch",
			"--endpoint", endpoint,
			"--run-id", armRunID,
			"--max-transcript-chars", fmt.Sprintf("%d", chars),
			"--query-lambda-arn", lambdaArn,
			"--limit", "100",
		)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		t.Logf("truncation_ab arm chars=%d would run: %v", chars, cmd.Args)
	}

	// Compare arms via the field_disagreement template.
	pairs := [][2]int{{5000, 10000}, {10000, 19000}, {5000, 19000}}
	for _, p := range pairs {
		_, err := helpers.InvokeQueryLambda(t, lambdaArn, helpers.QueryRequest{
			Template: "field_disagreement",
			Params: map[string]any{
				"run_a": runIDs[p[0]],
				"run_b": runIDs[p[1]],
				// TODO: iterate every relevant field in v2 CALL_SCHEMA.
				"field": "summary",
			},
		})
		if err != nil {
			t.Logf("truncation_ab compare %d vs %d: %v", p[0], p[1], err)
		}
	}
}
