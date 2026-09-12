package repro;

import com.google.protobuf.StringValue;
import io.grpc.MethodDescriptor;
import io.grpc.protobuf.ProtoUtils;

final class Echo {
  static final MethodDescriptor<StringValue, StringValue> METHOD =
      MethodDescriptor.<StringValue, StringValue>newBuilder()
          .setType(MethodDescriptor.MethodType.UNARY)
          .setFullMethodName("repro.Echo/UnaryEcho")
          .setRequestMarshaller(ProtoUtils.marshaller(StringValue.getDefaultInstance()))
          .setResponseMarshaller(ProtoUtils.marshaller(StringValue.getDefaultInstance()))
          .build();
}
