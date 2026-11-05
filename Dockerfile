FROM python:3.13-slim
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY . .
RUN useradd --uid 10001 --create-home app
USER app
CMD ["python", "-m", "payments.start"]
