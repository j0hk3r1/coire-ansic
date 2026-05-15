FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] httpx
COPY app.py /app/
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4002"]
