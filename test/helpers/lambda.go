package helpers

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/lambda"
	lambdatypes "github.com/aws/aws-sdk-go-v2/service/lambda/types"
)

// QueryRequest is the event payload accepted by the aws-llm-table-query Lambda.
type QueryRequest struct {
	SQL      string         `json:"sql,omitempty"`
	Template string         `json:"template,omitempty"`
	Params   map[string]any `json:"params,omitempty"`
}

// QueryResponse is the structured response from the Lambda.
type QueryResponse struct {
	Columns      []string `json:"columns"`
	Rows         [][]any  `json:"rows"`
	BytesScanned int64    `json:"bytes_scanned"`
	RuntimeMs    int64    `json:"runtime_ms"`
}

// InvokeQueryLambda invokes the bootstrap-owned aws-llm-table-query Lambda
// with InvocationType=RequestResponse and decodes the result.
func InvokeQueryLambda(t *testing.T, lambdaArn string, req QueryRequest) (*QueryResponse, error) {
	t.Helper()
	ctx := context.Background()
	cfg, err := awsconfig.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, err
	}
	client := lambda.NewFromConfig(cfg)

	payload, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}

	out, err := client.Invoke(ctx, &lambda.InvokeInput{
		FunctionName:   aws.String(lambdaArn),
		InvocationType: lambdatypes.InvocationTypeRequestResponse,
		Payload:        payload,
	})
	if err != nil {
		return nil, err
	}
	if out.FunctionError != nil && *out.FunctionError != "" {
		return nil, fmt.Errorf("lambda function error: %s: %s", *out.FunctionError, string(out.Payload))
	}

	var resp QueryResponse
	if err := json.Unmarshal(out.Payload, &resp); err != nil {
		return nil, fmt.Errorf("decode lambda response: %w; raw=%s", err, string(out.Payload))
	}
	return &resp, nil
}
