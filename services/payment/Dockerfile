FROM python:3.12-slim
WORKDIR /app
COPY services/payment/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY instrumentation/ instrumentation/
COPY services/payment/app.py app.py
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
