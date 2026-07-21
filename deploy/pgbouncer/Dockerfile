FROM alpine:3.22

RUN apk add --no-cache pgbouncer postgresql-client \
    && addgroup -g 10001 farescope \
    && adduser -D -H -u 10001 -G farescope farescope

COPY --chown=10001:10001 entrypoint.sh /usr/local/bin/farescope-pgbouncer

RUN chmod 0555 /usr/local/bin/farescope-pgbouncer

USER 10001:10001

EXPOSE 6432

ENTRYPOINT ["/usr/local/bin/farescope-pgbouncer"]
