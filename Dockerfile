FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# Server + 24/7 crawler ek hi process mein
CMD ["python", "-m", "engine", "serve", "--host", "0.0.0.0"]
