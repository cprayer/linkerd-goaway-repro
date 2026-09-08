package repro;

import com.google.protobuf.StringValue;
import io.grpc.CallOptions;
import io.grpc.ClientStreamTracer;
import io.grpc.ManagedChannel;
import io.grpc.Metadata;
import io.grpc.MethodDescriptor;
import io.grpc.Status;
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder;
import io.grpc.protobuf.ProtoUtils;
import io.grpc.stub.ClientCalls;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public final class GoAwayClient {
  public static void main(String[] args) throws Exception {
    String target = args[0];
    boolean disableRetry = Boolean.parseBoolean(args[1]);
    int requests = 20;
    var method = MethodDescriptor.<StringValue, StringValue>newBuilder()
        .setType(MethodDescriptor.MethodType.UNARY)
        .setFullMethodName("repro.Echo/UnaryEcho")
        .setRequestMarshaller(ProtoUtils.marshaller(StringValue.getDefaultInstance()))
        .setResponseMarshaller(ProtoUtils.marshaller(StringValue.getDefaultInstance()))
        .build();
    var success = new AtomicInteger();
    var failed = new AtomicInteger();
    var totalAttempts = new AtomicInteger();
    var transparentRetries = new AtomicInteger();
    var done = new CountDownLatch(requests);
    var executor = Executors.newFixedThreadPool(requests);
    List<ManagedChannel> channels = new ArrayList<>();
    System.out.printf("START time=%s target=%s disableRetry=%s%n", Instant.now(), target, disableRetry);
    for (int i = 0; i < requests; i++) {
      int id = i;
      var builder = NettyChannelBuilder.forTarget(target).usePlaintext().disableServiceConfigLookUp();
      if (disableRetry) builder.disableRetry();
      var channel = builder.build();
      channels.add(channel);
      executor.submit(() -> {
        var attempts = new AtomicInteger();
        var tracer = new ClientStreamTracer.Factory() {
          @Override
          public ClientStreamTracer newClientStreamTracer(ClientStreamTracer.StreamInfo info, Metadata headers) {
            int attempt = attempts.incrementAndGet();
            totalAttempts.incrementAndGet();
            if (info.isTransparentRetry()) transparentRetries.incrementAndGet();
            System.out.printf("ATTEMPT time=%s id=%d attempt=%d transparent=%s%n",
                Instant.now(), id, attempt, info.isTransparentRetry());
            return new ClientStreamTracer() {
              @Override
              public void streamClosed(Status status) {
                System.out.printf("STREAM_CLOSED time=%s id=%d attempt=%d code=%s description=%s%n",
                    Instant.now(), id, attempt, status.getCode(),
                    String.valueOf(status.getDescription()).replace('\n', ' '));
              }
            };
          }
        };
        String message = "goaway-" + id;
        try {
          var response = ClientCalls.blockingUnaryCall(channel, method,
              CallOptions.DEFAULT.withDeadlineAfter(90, TimeUnit.SECONDS).withStreamTracerFactory(tracer),
              StringValue.of(message));
          if (!message.equals(response.getValue())) throw new IllegalStateException("Echo mismatch");
          success.incrementAndGet();
          System.out.printf("RPC id=%d code=OK attempts=%d%n", id, attempts.get());
        } catch (Exception error) {
          failed.incrementAndGet();
          System.out.printf("RPC id=%d code=%s attempts=%d error=%s%n", id,
              Status.fromThrowable(error).getCode(), attempts.get(), error.toString().replace('\n', ' '));
        } finally {
          done.countDown();
        }
      });
      if (i + 1 < requests) Thread.sleep(1000);
    }
    boolean completed = done.await(100, TimeUnit.SECONDS);
    executor.shutdownNow();
    for (var channel : channels) channel.shutdownNow();
    System.out.printf("SUMMARY {\"completed\":%s,\"requests\":%d,\"success\":%d,\"failed\":%d,\"attempts\":%d,\"transparentRetries\":%d,\"disableRetry\":%s}%n",
        completed, requests, success.get(), failed.get(), totalAttempts.get(), transparentRetries.get(), disableRetry);
    new CountDownLatch(1).await();
  }
}
