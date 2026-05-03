package helpers

import (
	"os"
	"strconv"
	"testing"

	"github.com/gruntwork-io/terratest/modules/terraform"
)

// TerraformOptionsRuntime returns terraform.Options for the runtime stack,
// pulling overrides (use_spot, instance_type, etc.) from the environment.
func TerraformOptionsRuntime(t *testing.T) *terraform.Options {
	t.Helper()
	vars := map[string]any{}
	if v := os.Getenv("USE_SPOT"); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			vars["use_spot"] = b
		}
	}
	if v := os.Getenv("INSTANCE_TYPE"); v != "" {
		vars["instance_type"] = v
	}
	if v := os.Getenv("VLLM_IMAGE_URI"); v != "" {
		vars["vllm_image_uri"] = v
	}
	if v := os.Getenv("MAX_MODEL_LEN"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			vars["max_model_len"] = n
		}
	}
	if v := os.Getenv("MAX_NUM_SEQS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			vars["max_num_seqs"] = n
		}
	}
	if v := os.Getenv("ENABLE_THINKING"); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			vars["enable_thinking"] = b
		}
	}
	if v := os.Getenv("REGION"); v != "" {
		vars["region"] = v
	}
	if v := os.Getenv("SPOT_MAX_PRICE"); v != "" {
		vars["spot_max_price"] = v
	}

	return &terraform.Options{
		TerraformDir: "../infra/runtime",
		Vars:         vars,
		NoColor:      true,
	}
}

// RuntimeStackUp runs terraform.InitAndApply on the runtime stack and returns
// the options plus the vllm_endpoint output.
func RuntimeStackUp(t *testing.T) (*terraform.Options, string) {
	t.Helper()
	opts := TerraformOptionsRuntime(t)
	terraform.InitAndApply(t, opts)
	endpoint := terraform.Output(t, opts, "vllm_endpoint")
	return opts, endpoint
}

// RuntimeStackDown runs terraform.Destroy on the runtime stack.
func RuntimeStackDown(t *testing.T, opts *terraform.Options) {
	t.Helper()
	terraform.Destroy(t, opts)
}
