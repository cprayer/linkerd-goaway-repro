package repro;

import com.google.protobuf.StringValue;
import io.grpc.ServerServiceDefinition;
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder;
import io.grpc.stub.ServerCallStreamObserver;
import io.grpc.stub.ServerCalls;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public final class GoAwayServer {
  public static void main(String[] args) throws Exception {
    int port = Integer.parseInt(args[0]);
    var timer = Executors.newSingleThreadScheduledExecutor();
    var service = ServerServiceDefinition.builder("repro.Echo")
        .addMethod(Echo.METHOD, ServerCalls.asyncUnaryCall((request, observer) -> {
          System.out.printf("ACCEPT message=%s%n", request.getValue());
          var response = timer.schedule(() -> {
            observer.onNext(request);
            observer.onCompleted();
          }, 30, TimeUnit.SECONDS);
          ((ServerCallStreamObserver<StringValue>) observer).setOnCancelHandler(() -> response.cancel(false));
        })).build();
    var server = NettyServerBuilder.forPort(port)
        .maxConnectionAge(5, TimeUnit.SECONDS)
        .maxConnectionAgeGrace(1, TimeUnit.MINUTES)
        .maxConnectionIdle(2, TimeUnit.MINUTES)
        .addService(service).build().start();
    Runtime.getRuntime().addShutdownHook(new Thread(() -> {
      server.shutdownNow();
      timer.shutdownNow();
    }));
    System.out.printf("READY port=%d max_connection_age=5s response_delay=30s%n", port);
    server.awaitTermination();
  }
}
