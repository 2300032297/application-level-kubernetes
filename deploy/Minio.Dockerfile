FROM golang:1.24.8-alpine AS builder
ENV CGO_ENABLED=0 GOMAXPROCS=2 GOMEMLIMIT=600MiB
RUN go install -p=1 -trimpath -ldflags="-s -w" github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z
FROM alpine:3.22
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /go/bin/minio /usr/local/bin/minio
USER 10001:10001
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
