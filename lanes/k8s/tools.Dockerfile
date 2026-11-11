# The load-test kit's Go tools (kit/cmd/*) and workloads on alpine/k8s instead of the kit's
# distroless image: the in-cluster runner and the seed Job need a shell, and the runner reads pod
# CPU and memory from metrics.k8s.io with kubectl and jq. Build context: the kit directory
# (bench.sh images does this).
FROM golang:1.24.7-bookworm@sha256:b8bae5bd9ba9b1f89b635c91c24cc75cea335a16fb5076310f38566fc674b1ec AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ ./cmd/...

FROM alpine/k8s:1.35.6@sha256:b7a12c5ddf261994c33d2eaaa06fd69a0803ff6b38683bfa3d30a76dcdf92807
# psql: run.sh reads each side's records back for verify (FEATURES=full).
RUN apk add --no-cache postgresql-client
COPY --from=build /out/ /usr/local/bin/
COPY workloads /workloads
WORKDIR /work
