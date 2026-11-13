FROM python:3.12-slim

WORKDIR /srv
ENV HOST=0.0.0.0 PORT=8400 DATA_DIR=/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run.py .
RUN mkdir -p /data
VOLUME /data
CMD ["python", "run.py"]
