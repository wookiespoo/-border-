FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt* ./
RUN pip install --no-cache-dir \
    flask requests cryptography \
    || true

COPY . .
RUN pip install --no-cache-dir -e . || true

EXPOSE 9000
CMD ["python", "-m", "border.node_runner", "--host", "0.0.0.0", "--port", "9000"]
