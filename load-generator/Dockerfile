FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx
COPY load-generator/generate.py generate.py
CMD ["python", "generate.py"]
