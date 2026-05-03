package helpers

import (
	"context"
	"errors"
	"os"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
)

// s3Client builds a default S3 client. Stubs are fine for build-check purposes.
func s3Client(ctx context.Context) (*s3.Client, error) {
	cfg, err := awsconfig.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, err
	}
	return s3.NewFromConfig(cfg), nil
}

// UploadFile uploads localPath to s3://bucket/key.
func UploadFile(t *testing.T, bucket, key, localPath string) error {
	t.Helper()
	ctx := context.Background()
	client, err := s3Client(ctx)
	if err != nil {
		return err
	}
	f, err := os.Open(localPath)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
		Body:   f,
	})
	return err
}

// DownloadFile downloads s3://bucket/key to localPath.
func DownloadFile(t *testing.T, bucket, key, localPath string) error {
	t.Helper()
	ctx := context.Background()
	client, err := s3Client(ctx)
	if err != nil {
		return err
	}
	out, err := client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return err
	}
	defer out.Body.Close()
	f, err := os.Create(localPath)
	if err != nil {
		return err
	}
	defer f.Close()
	buf := make([]byte, 64*1024)
	for {
		n, rerr := out.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				return werr
			}
		}
		if rerr != nil {
			if errors.Is(rerr, errors.New("EOF")) {
				break
			}
			break
		}
	}
	return nil
}

// KeyExists returns true if s3://bucket/key exists.
func KeyExists(t *testing.T, bucket, key string) (bool, error) {
	t.Helper()
	ctx := context.Background()
	client, err := s3Client(ctx)
	if err != nil {
		return false, err
	}
	_, err = client.HeadObject(ctx, &s3.HeadObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		var nf *types.NotFound
		if errors.As(err, &nf) {
			return false, nil
		}
		return false, err
	}
	return true, nil
}
