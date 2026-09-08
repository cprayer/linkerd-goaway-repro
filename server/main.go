package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

type echoService interface {
	UnaryEcho(context.Context, *wrapperspb.StringValue) (*wrapperspb.StringValue, error)
}

type echoServer struct{ delay time.Duration }

func (s echoServer) UnaryEcho(ctx context.Context, req *wrapperspb.StringValue) (*wrapperspb.StringValue, error) {
	log.Printf("ACCEPT message=%s", req.Value)
	timer := time.NewTimer(s.delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		return req, nil
	case <-ctx.Done():
		return nil, status.FromContextError(ctx.Err()).Err()
	}
}

func main() {
	port := flag.Int("port", 50052, "listen port")
	age := flag.Duration("max-connection-age", 5*time.Second, "connection age before graceful GOAWAY")
	delay := flag.Duration("response-delay", 30*time.Second, "delay before echo response")
	flag.Parse()
	listener, err := net.Listen("tcp", fmt.Sprintf(":%d", *port))
	if err != nil {
		log.Fatal(err)
	}
	server := grpc.NewServer(grpc.KeepaliveParams(keepalive.ServerParameters{
		MaxConnectionAge:      *age,
		MaxConnectionAgeGrace: time.Minute,
		MaxConnectionIdle:     2 * time.Minute,
	}))
	server.RegisterService(&grpc.ServiceDesc{
		ServiceName: "repro.Echo",
		HandlerType: (*echoService)(nil),
		Methods: []grpc.MethodDesc{{
			MethodName: "UnaryEcho",
			Handler: func(srv any, ctx context.Context, decode func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error) {
				req := new(wrapperspb.StringValue)
				if err := decode(req); err != nil {
					return nil, err
				}
				handler := func(ctx context.Context, req any) (any, error) {
					return srv.(echoService).UnaryEcho(ctx, req.(*wrapperspb.StringValue))
				}
				if interceptor == nil {
					return handler(ctx, req)
				}
				return interceptor(ctx, req, &grpc.UnaryServerInfo{
					Server: srv, FullMethod: "/repro.Echo/UnaryEcho",
				}, handler)
			},
		}},
	}, echoServer{delay: *delay})
	log.Printf("READY port=%d max_connection_age=%s response_delay=%s", *port, *age, *delay)
	log.Fatal(server.Serve(listener))
}
