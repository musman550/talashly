FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 1000 app && mkdir -p /app/data && chown -R app /app
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# Server + chat + crawler ek hi process mein. Hosts PORT env dete hain (engine use khud padhta hai).
CMD ["python", "-m", "engine", "serve", "--host", "0.0.0.0"]
