// Package buildcheck exists solely to force `go build ./...` to compile every
// helpers/* and experiments/* package in the test module. Go skips _test.go
// files under build, so without this shim the packages would only be
// type-checked at `go test` time.
package buildcheck

import (
	_ "github.com/your-org/aws-llm/test/experiments"
	_ "github.com/your-org/aws-llm/test/helpers"
)
