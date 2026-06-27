FROM minio/mc:latest AS mc-stage

FROM minio/minio:RELEASE.2024-01-01T16-36-33Z
# Add mc client so smoke tests can run 'mc ls' inside this pod
COPY --from=mc-stage /usr/bin/mc /usr/local/bin/mc
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
