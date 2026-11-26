FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c
WORKDIR /app
COPY kube_recover /app/kube_recover
COPY data/orders-v1.db /app/data/orders-v1.db
RUN useradd --uid 10001 --create-home recover && mkdir /data && chown recover:recover /data
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python","-m","kube_recover","console","--root","/data","--host","0.0.0.0","--port","8080"]
