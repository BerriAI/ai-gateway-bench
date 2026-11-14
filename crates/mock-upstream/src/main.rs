#![forbid(unsafe_code)]

use std::convert::Infallible;
use std::env;
use std::net::IpAddr;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Json, State};
use axum::http::header::CONTENT_TYPE;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use bytes::Bytes;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

const WORD: &str = "token ";
const TOOL_FRAGMENTS: [&str; 5] = [
    r#"{"path":"#,
    r#" "src/main"#,
    r#".rs","content":"#,
    r#" "fn main"#,
    r#"() {}"}"#,
];

#[derive(Clone)]
struct MockConfig {
    ttft: Duration,
    itl: Duration,
    output_tokens: usize,
    fail_status: Option<u16>,
}

impl MockConfig {
    fn from_env() -> Self {
        let ttft_ms = env::var("MOCK_TTFT_MS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(20);
        let itl_ms = env::var("MOCK_ITL_MS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(5);
        let output_tokens = env::var("MOCK_OUTPUT_TOKENS")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(40);
        let fail_status = env::var("MOCK_FAIL_STATUS")
            .ok()
            .and_then(|value| value.parse::<u16>().ok())
            .filter(|status| (400..=599).contains(status));
        Self {
            ttft: Duration::from_millis(ttft_ms),
            itl: Duration::from_millis(itl_ms),
            output_tokens,
            fail_status,
        }
    }
}

#[tokio::main(flavor = "multi_thread", worker_threads = 8)]
async fn main() {
    let config = MockConfig::from_env();
    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/messages", post(messages))
        .route("/v1/chat/completions", post(chat_completions))
        .with_state(config);
    let address: IpAddr = env::var("MOCK_HOST")
        .unwrap_or_else(|_| "127.0.0.1".to_string())
        .parse()
        .expect("MOCK_HOST must be a valid IP address");
    let port = env::var("MOCK_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(9000);
    let listener = tokio::net::TcpListener::bind((address, port))
        .await
        .expect("mock upstream binds");
    // TCP_NODELAY: without it a streamed response of many small SSE writes can stall
    // ~40 ms on the last small segment (Nagle waiting for the peer's delayed ACK) — a
    // cost the non-streaming path never pays, so it skews any overhead comparison.
    let listener = axum::serve::ListenerExt::tap_io(listener, |tcp_stream: &mut tokio::net::TcpStream| {
        let _ = tcp_stream.set_nodelay(true);
    });
    axum::serve(listener, app)
        .await
        .expect("mock upstream serves");
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok"}))
}

fn failure_response(config: &MockConfig) -> Option<Response> {
    config.fail_status.map(|status| {
        (
            StatusCode::from_u16(status).expect("failure status is in HTTP error range"),
            Json(json!({
                "error": {
                    "message": "deterministic mock failure",
                    "status": status
                }
            })),
        )
            .into_response()
    })
}

fn wants_tool_call(body: &Value) -> bool {
    body.get("tools").is_some_and(value_is_truthy)
        || body.get("_gwbench_tool").and_then(Value::as_bool) == Some(true)
}

fn value_is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn anthropic_body(config: &MockConfig, tool_call: bool) -> Value {
    let content = if tool_call {
        json!([{
            "type": "tool_use",
            "id": "toolu_gwbench",
            "name": "edit_file",
            "input": {
                "path": "src/main.rs",
                "content": "fn main() {}"
            }
        }])
    } else {
        json!([{"type": "text", "text": WORD.repeat(config.output_tokens)}])
    };
    json!({
        "id": "msg_gwbench",
        "type": "message",
        "role": "assistant",
        "model": "mock-upstream",
        "content": content,
        "stop_reason": if tool_call { "tool_use" } else { "end_turn" },
        "stop_sequence": null,
        "usage": {
            "input_tokens": 1,
            "output_tokens": config.output_tokens
        }
    })
}

fn sse_event(kind: &str, payload: Value) -> Bytes {
    Bytes::from(format!(
        "event: {kind}\ndata: {}\n\n",
        serde_json::to_string(&payload).expect("SSE payload serializes")
    ))
}

fn anthropic_stream(
    config: MockConfig,
    tool_call: bool,
) -> ReceiverStream<Result<Bytes, Infallible>> {
    let (sender, receiver) = mpsc::channel(16);
    tokio::spawn(async move {
        let message_start = json!({
            "type": "message_start",
            "message": {
                "id": "msg_gwbench",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "mock-upstream",
                "stop_reason": null,
                "stop_sequence": null,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": config.output_tokens
                }
            }
        });
        if sender
            .send(Ok(sse_event("message_start", message_start)))
            .await
            .is_err()
        {
            return;
        }
        if !config.ttft.is_zero() {
            tokio::time::sleep(config.ttft).await;
        }
        if tool_call {
            let start = json!({
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_gwbench",
                    "name": "edit_file",
                    "input": {}
                }
            });
            if sender
                .send(Ok(sse_event("content_block_start", start)))
                .await
                .is_err()
            {
                return;
            }
            for fragment in TOOL_FRAGMENTS {
                if !config.itl.is_zero() {
                    tokio::time::sleep(config.itl).await;
                }
                let delta = json!({
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": fragment
                    }
                });
                if sender
                    .send(Ok(sse_event("content_block_delta", delta)))
                    .await
                    .is_err()
                {
                    return;
                }
            }
            let stop = json!({"type": "content_block_stop", "index": 0});
            if sender
                .send(Ok(sse_event("content_block_stop", stop)))
                .await
                .is_err()
            {
                return;
            }
        } else {
            let start = json!({
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "text",
                    "text": ""
                }
            });
            if sender
                .send(Ok(sse_event("content_block_start", start)))
                .await
                .is_err()
            {
                return;
            }
            for _ in 0..config.output_tokens {
                if !config.itl.is_zero() {
                    tokio::time::sleep(config.itl).await;
                }
                let delta = json!({
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": WORD
                    }
                });
                if sender
                    .send(Ok(sse_event("content_block_delta", delta)))
                    .await
                    .is_err()
                {
                    return;
                }
            }
            let stop = json!({"type": "content_block_stop", "index": 0});
            if sender
                .send(Ok(sse_event("content_block_stop", stop)))
                .await
                .is_err()
            {
                return;
            }
        }
        let message_delta = json!({
            "type": "message_delta",
            "delta": {
                "stop_reason": if tool_call { "tool_use" } else { "end_turn" }
            },
            "usage": {"output_tokens": config.output_tokens}
        });
        if sender
            .send(Ok(sse_event("message_delta", message_delta)))
            .await
            .is_err()
        {
            return;
        }
        let _ = sender
            .send(Ok(sse_event(
                "message_stop",
                json!({"type": "message_stop"}),
            )))
            .await;
    });
    ReceiverStream::new(receiver)
}

async fn messages(State(config): State<MockConfig>, Json(body): Json<Value>) -> Response {
    if let Some(response) = failure_response(&config) {
        return response;
    }
    let tool_call = wants_tool_call(&body);
    if body.get("stream").and_then(Value::as_bool) == Some(true) {
        return Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "text/event-stream")
            .body(Body::from_stream(anthropic_stream(config, tool_call)))
            .expect("stream response builds");
    }
    if !config.ttft.is_zero() {
            tokio::time::sleep(config.ttft).await;
        }
    Json(anthropic_body(&config, tool_call)).into_response()
}

fn openai_body(config: &MockConfig) -> Value {
    json!({
        "id": "chatcmpl-gwbench",
        "object": "chat.completion",
        "created": 0,
        "model": "mock-upstream",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": WORD.repeat(config.output_tokens)
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": config.output_tokens,
            "total_tokens": config.output_tokens + 1
        }
    })
}

fn openai_stream(config: MockConfig) -> ReceiverStream<Result<Bytes, Infallible>> {
    let (sender, receiver) = mpsc::channel(16);
    tokio::spawn(async move {
        if !config.ttft.is_zero() {
            tokio::time::sleep(config.ttft).await;
        }
        if sender
            .send(Ok(Bytes::from(format!(
                "data: {}\n\n",
                serde_json::to_string(&json!({
                    "id": "chatcmpl-gwbench",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "mock-upstream",
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": null
                    }]
                }))
                .expect("SSE payload serializes")
            ))))
            .await
            .is_err()
        {
            return;
        }
        for _ in 0..config.output_tokens {
            if !config.itl.is_zero() {
                    tokio::time::sleep(config.itl).await;
                }
            let payload = json!({
                "id": "chatcmpl-gwbench",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "mock-upstream",
                "choices": [{
                    "index": 0,
                    "delta": {"content": WORD},
                    "finish_reason": null
                }]
            });
            if sender
                .send(Ok(Bytes::from(format!(
                    "data: {}\n\n",
                    serde_json::to_string(&payload).expect("SSE payload serializes")
                ))))
                .await
                .is_err()
            {
                return;
            }
        }
        let finish = json!({
            "id": "chatcmpl-gwbench",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "mock-upstream",
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        });
        if sender
            .send(Ok(Bytes::from(format!(
                "data: {}\n\n",
                serde_json::to_string(&finish).expect("SSE payload serializes")
            ))))
            .await
            .is_ok()
        {
            let _ = sender
                .send(Ok(Bytes::from_static(b"data: [DONE]\n\n")))
                .await;
        }
    });
    ReceiverStream::new(receiver)
}

async fn chat_completions(State(config): State<MockConfig>, Json(body): Json<Value>) -> Response {
    if let Some(response) = failure_response(&config) {
        return response;
    }
    if body.get("stream").and_then(Value::as_bool) == Some(true) {
        return Response::builder()
            .status(StatusCode::OK)
            .header(CONTENT_TYPE, "text/event-stream")
            .body(Body::from_stream(openai_stream(config)))
            .expect("stream response builds");
    }
    if !config.ttft.is_zero() {
            tokio::time::sleep(config.ttft).await;
        }
    Json(openai_body(&config)).into_response()
}
