FROM python:3.13-slim
WORKDIR /app
# Pinned for reproducible builds.
RUN pip install --no-cache-dir \
      "fastapi==0.115.6" \
      "uvicorn[standard]==0.32.1" \
      "httpx==0.27.2"
COPY app.py /app/
EXPOSE 4002
# Honor $PORT (defaults to 4002) so the listen port matches the app's PORT env.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-4002}"]
