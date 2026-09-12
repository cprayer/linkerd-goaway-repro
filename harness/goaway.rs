use linkerd2_proxy_api::{meta, outbound};
use linkerd_app_integration::{controller, identity, policy, proxy, trace_init};
use std::{env, fs, net::SocketAddr, time::Duration};

fn metadata(kind: &str, name: &str) -> meta::Metadata {
    meta::Metadata {
        kind: Some(meta::metadata::Kind::Resource(meta::Resource {
            group: "gateway.networking.k8s.io".into(),
            kind: kind.into(),
            name: name.into(),
            namespace: "goaway-repro".into(),
            ..Default::default()
        })),
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let _trace = trace_init();
    let args: Vec<_> = env::args().collect();
    assert_eq!(args.len(), 4, "usage: goaway CASE BACKEND READY_FILE");
    let label = &args[1];
    assert!(matches!(
        label.as_str(),
        "baseline-one"
            | "baseline-two"
            | "patched-one"
            | "patched-two"
            | "patched-one-no-retry"
            | "patched-two-no-retry"
            | "patched-policy"
    ));
    let target: SocketAddr = args[2].parse().expect("backend address");
    assert!(target.ip().is_loopback(), "backend must be loopback");
    let host = format!("grpc-{label}.goaway-repro.svc.cluster.local");
    let authority = format!("{host}:{}", target.port());
    let mut profiles = Vec::new();
    let inbound_identity = "foo.ns1.serviceaccount.identity.linkerd.cluster.local";
    let inbound = if label.contains("-two") {
        let id = identity::Identity::new("foo-ns1", inbound_identity.into());
        let ctrl = controller::new();
        profiles.push(ctrl.profile_tx_default(authority.as_str(), host.as_str()));
        Some(
            proxy::new()
                .controller(ctrl.run().await)
                .identity(id.service().run().await)
                .inbound_ip(target)
                .inbound_direct()
                .run_with_test_env(id.env)
                .await,
        )
    } else {
        None
    };

    let ctrl = controller::new();
    let destination = ctrl.destination_tx(authority.as_str());
    let mut endpoint = controller::destination_add(target).hint(controller::Hint::H2);
    if let Some(inbound) = &inbound {
        endpoint = endpoint
            .opaque_port(inbound.inbound.port())
            .identity(inbound_identity);
    }
    destination.send(endpoint);
    profiles.push(ctrl.profile_tx_default(target, host.as_str()));
    let retry = (label == "patched-policy").then(|| outbound::grpc_route::Retry {
        max_retries: 1,
        max_request_bytes: 64 * 1024,
        conditions: Some(outbound::grpc_route::retry::Conditions {
            unavailable: true,
            ..Default::default()
        }),
        timeout: Some(Duration::from_secs(60).try_into().expect("retry timeout")),
        backoff: None,
    });
    let route = outbound::GrpcRoute {
        metadata: Some(metadata("GRPCRoute", label)),
        hosts: Vec::new(),
        rules: vec![outbound::grpc_route::Rule {
            backends: Some(outbound::grpc_route::Distribution {
                kind: Some(outbound::grpc_route::distribution::Kind::FirstAvailable(
                    outbound::grpc_route::distribution::FirstAvailable {
                        backends: vec![outbound::grpc_route::RouteBackend {
                            backend: Some(policy::backend(&authority)),
                            ..Default::default()
                        }],
                    },
                )),
            }),
            retry,
            ..Default::default()
        }],
    };
    let policies = controller::policy()
        .with_inbound_default(policy::all_unauthenticated())
        .outbound(
            target,
            outbound::OutboundPolicy {
                metadata: Some(metadata("Service", label)),
                protocol: Some(outbound::ProxyProtocol {
                    kind: Some(outbound::proxy_protocol::Kind::Grpc(
                        outbound::proxy_protocol::Grpc {
                            routes: vec![route],
                            ..Default::default()
                        },
                    )),
                }),
            },
        );
    let outbound_identity = identity::Identity::new(
        "bar-ns1",
        "bar.ns1.serviceaccount.identity.linkerd.cluster.local".into(),
    );
    let outbound = proxy::new()
        .controller(ctrl.run().await)
        .policy(policies.run().await)
        .identity(outbound_identity.service().run().await)
        .outbound_ip(target)
        .run_with_test_env(outbound_identity.env)
        .await;
    eprintln!("release {}", env!("LINKERD2_PROXY_VERSION"));
    fs::write(
        &args[3],
        serde_json::to_vec(&serde_json::json!({
            "outbound": outbound.outbound.to_string(),
            "admin": outbound.admin.to_string(),
            "authority": authority,
        }))
        .expect("serialize listeners"),
    )
    .expect("write listeners");
    std::future::pending::<()>().await;
    drop((destination, profiles, inbound, outbound));
}
