FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY ovcatch ovcatch
COPY web web
COPY config.yaml config.demo.yaml ./
EXPOSE 8000
CMD ["python", "-m", "ovcatch", "serve", "--host", "0.0.0.0", "--port", "8000"]
