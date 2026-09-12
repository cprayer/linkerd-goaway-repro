package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"example.com/linkerd-goaway-repro/server/echo"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
)

type trace struct {
	id                int
	attempts, retries atomic.Int32
}

func (t *trace) TagRPC(ctx context.Context, _ *stats.RPCTagInfo) context.Context   { return ctx }
func (t *trace) TagConn(ctx context.Context, _ *stats.ConnTagInfo) context.Context { return ctx }
func (t *trace) HandleConn(context.Context, stats.ConnStats)                       {}
func (t *trace) HandleRPC(_ context.Context, event stats.RPCStats) {
	switch event := event.(type) {
	case *stats.Begin:
		attempt := t.attempts.Add(1)
		if event.IsTransparentRetryAttempt {
			t.retries.Add(1)
		}
		fmt.Printf("ATTEMPT time=%s id=%d attempt=%d transparent=%t\n", time.Now().UTC().Format(time.RFC3339Nano), t.id, attempt, event.IsTransparentRetryAttempt)
	case *stats.End:
		fmt.Printf("STREAM_CLOSED time=%s id=%d attempt=%d code=%s description=%s\n", time.Now().UTC().Format(time.RFC3339Nano), t.id, t.attempts.Load(), strings.ToUpper(status.Code(event.Error).String()), strings.ReplaceAll(status.Convert(event.Error).Message(), "\n", " "))
	}
}

func main() {
	disabled, err := strconv.ParseBool(os.Args[2])
	if err != nil {
		panic(err)
	}
	type result struct {
		success           bool
		attempts, retries int32
	}
	results := make(chan result, 20)
	for id := 0; id < 20; id++ {
		go func(id int) {
			tracer := &trace{id: id}
			options := []grpc.DialOption{grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDisableServiceConfig(), grpc.WithStatsHandler(tracer)}
			if disabled {
				options = append(options, grpc.WithDisableRetry())
			}
			conn, err := grpc.NewClient(os.Args[1], options...)
			if err != nil {
				panic(err)
			}
			defer conn.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
			defer cancel()
			message := fmt.Sprintf("goaway-%d", id)
			response, err := echo.Call(ctx, conn, message)
			if err == nil && response != message {
				err = fmt.Errorf("echo mismatch: %q", response)
			}
			fmt.Printf("RPC id=%d code=%s attempts=%d error=%v\n", id, strings.ToUpper(status.Code(err).String()), tracer.attempts.Load(), err)
			results <- result{err == nil, tracer.attempts.Load(), tracer.retries.Load()}
		}(id)
		if id < 19 {
			time.Sleep(time.Second)
		}
	}
	success, attempts, retries := 0, 0, 0
	for range 20 {
		r := <-results
		if r.success {
			success++
		}
		attempts += int(r.attempts)
		retries += int(r.retries)
	}
	summary, err := json.Marshal(map[string]any{"completed": true, "requests": 20, "success": success, "failed": 20 - success, "attempts": attempts, "transparentRetries": retries, "disableRetry": disabled})
	if err != nil {
		panic(err)
	}
	fmt.Printf("SUMMARY %s\n", summary)
	time.Sleep(24 * time.Hour)
}
