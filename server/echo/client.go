package echo

import (
	"context"

	"example.com/linkerd-goaway-repro/server/rpc"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

func Call(ctx context.Context, conn grpc.ClientConnInterface, message string) (string, error) {
	response, err := rpc.NewEchoClient(conn).UnaryEcho(ctx, wrapperspb.String(message))
	if err != nil {
		return "", err
	}
	return response.Value, nil
}
