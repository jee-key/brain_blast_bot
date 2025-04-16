FROM python:3.11-slim

WORKDIR /app

# Create a persistent volume for the database
RUN mkdir -p /data
# VOLUME /data

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
