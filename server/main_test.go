package main

import (
	"context"
	"net"
	"testing"
	"time"

	"example.com/linkerd-goaway-repro/server/echo"
	"example.com/linkerd-goaway-repro/server/rpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

func TestEcho(t *testing.T) {
	listener := bufconn.Listen(1024 * 1024)
	server := grpc.NewServer()
	rpc.RegisterEchoServer(server, echoServer{})
	go server.Serve(listener)
	t.Cleanup(server.Stop)
	conn, err := grpc.NewClient("passthrough:///echo", grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	got, err := echo.Call(ctx, conn, "goaway-test")
	if err != nil || got != "goaway-test" {
		t.Fatalf("response=%q error=%v", got, err)
	}
	cancel()
	_, err = (echoServer{delay: time.Minute}).UnaryEcho(ctx, wrapperspb.String("cancelled"))
	if status.Code(err) != codes.Canceled {
		t.Fatalf("cancellation: %v", err)
	}
}
