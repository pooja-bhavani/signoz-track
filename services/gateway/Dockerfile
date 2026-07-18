FROM python:3.12-slim
WORKDIR /app
COPY services/gateway/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY instrumentation/ instrumentation/
COPY services/gateway/app.py app.py
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
