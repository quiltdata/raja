FROM python:3.12-slim

WORKDIR /app

COPY lambda_handlers/authorizer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src/raja /app/src/raja
COPY lambda_handlers/authorizer /app/authorizer

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "authorizer.app:app", "--host", "0.0.0.0", "--port", "9000"]
