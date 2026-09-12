package repro;

import com.google.protobuf.StringValue;
import io.grpc.CallOptions;
import io.grpc.Channel;
import io.grpc.stub.ClientCalls;

public final class GoAwayClient {
  public static String echo(Channel channel, String message, CallOptions options) {
    return ClientCalls.blockingUnaryCall(channel, Echo.METHOD, options, StringValue.of(message)).getValue();
  }
}
