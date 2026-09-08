FROM maven:3.9.9-eclipse-temurin-17@sha256:f58d59b6273e785ac0a4477f6e9b5ba1d7731c75b906c0f7b34076f1851318cc AS build
WORKDIR /src
COPY pom.xml ./
COPY src ./src
RUN mvn -B -ntp package

FROM eclipse-temurin:17-jre@sha256:13cc28a6cc72a38ce1f00c906be3580c1a3e604b8984d694f369a96742abc93b
COPY --from=build /src/target/client.jar /client.jar
USER 65532:65532
ENTRYPOINT ["java", "-XX:ActiveProcessorCount=2", "-Xms32m", "-Xmx128m", "-jar", "/client.jar"]
